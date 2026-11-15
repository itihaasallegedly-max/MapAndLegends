"""Look at what Flow drew before it is published.

29 Sep: the French Guiana reel showed India's State Emblem with a garbled motto,
rupee notes and an Indian passport while the narrator talked about France.
Nothing between Flow and the upload ever looked at the picture. This does:
three frames of each raw clip (before our labels/locator are overlaid) go to a
Gemini vision model together with the line spoken over them, and it reports
anything a viewer would call wrong.

Symbols are fine when they are the right ones: France's flag, emblem, the euro
or a French passport in a clip about France. Blocking kinds:
wrong_symbol (a flag, emblem, seal, currency, document or landmark of a country
the line is not about, or a real one drawn wrong), contradiction (the picture
shows something the line does not say, or the opposite), text (garbled,
misspelled or invented writing large enough to notice).
"""
import json
import os
import re
import subprocess
from pathlib import Path

MODEL = os.getenv("VISION_CHECK_MODEL", "gemini-3.5-flash-lite")
BLOCKING = {"wrong_symbol", "contradiction", "text"}

PROMPT = """You check one short clip of a Hindi geography video before it is published.
You see {n} frames taken across the clip. The video is about: {topic}
The narrator says over this clip (Hindi): "{line}"

Report ONLY real problems a viewer would notice. DO NOT fact-check the narration itself; assume the narration is factually correct and evaluate only if the images contradict it:
- wrong_symbol: a flag, national emblem, seal, coat of arms, currency, passport/document or landmark that belongs to a country or place the line and the topic are NOT about (e.g. India's emblem or rupees in a clip about France), or a real one drawn clearly wrong (wrong colours, garbled design)
- contradiction: the picture clearly shows something the line does not say, or the opposite of it
- text: garbled, misspelled or invented letters or words large enough to notice (ignore tiny specks and correct real writing)

Correct symbols of the place the line is about (its real flag, emblem, currency, passport, landmarks) are FINE — do not report them.
A plain map, landscape, people, animals or buildings are fine.
Return JSON only: {{"problems": [{{"kind": "<one of the kinds>", "what": "<short English description>"}}]}}
Return {{"problems": []}} if the clip is fine."""


def _duration(clip):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(clip)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def frames(clip, points=(0.2, 0.5, 0.85), height=640):
    """JPEG bytes of a few frames across the clip."""
    d = _duration(clip)
    got = []
    for p in points:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, d * p):.2f}", "-i", str(clip),
                            "-frames:v", "1", "-vf", f"scale=-2:{height}", "-f", "image2pipe",
                            "-vcodec", "mjpeg", "-"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            got.append(r.stdout)
    return got


def _parse(text):
    text = str(text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in vision reply: {text[:120]}")
    data = json.loads(m.group(0))
    probs = []
    for p in data.get("problems") or []:
        if isinstance(p, dict):
            kind = str(p.get("kind") or "").strip().lower()
            probs.append({"kind": kind, "what": str(p.get("what") or "").strip()})
    return probs


def check_clip(client, clip, line, topic, call_model, types):
    """Problems in one clip: [{kind, what}], blocking ones only."""
    imgs = frames(clip)
    if not imgs:
        raise ValueError(f"could not read frames from {Path(clip).name}")
    contents = [PROMPT.format(n=len(imgs), topic=topic or "geography", line=line or "(no narration)")]
    contents += [types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in imgs]

    def ask(model):
        resp = client.models.generate_content(model=model, contents=contents)
        return _parse(resp.text)

    last = None
    for _ in range(2):                     # one retry on an unreadable reply
        try:
            probs, _m = call_model(ask, models=[MODEL], what="visual check")
            return [p for p in probs if p["kind"] in BLOCKING]
        except ValueError as e:
            last = e
    raise last


def avoid_line(problems):
    """A sentence appended to the Flow prompt when the clip is regenerated."""
    what = "; ".join(p["what"] for p in problems if p.get("what"))[:300]
    return ("AVOID (the previous take was rejected for this): " + what + ". "
            "Any flag, emblem, currency or document shown must be the real one of the place "
            "this line is about, with no garbled writing.")


def sanitize_prompt(prompt, problems):
    """If visual check failed due to garbled text or wrong map boundaries,
    strip cartographic triggers from the prompt so Flow draws pure scenery/landmarks."""
    kinds = {p.get("kind") for p in problems}
    new_prompt = prompt
    if "text" in kinds or "contradiction" in kinds:
        # Replace map and cartographic keywords with pure visual landscape
        new_prompt = re.sub(r"\b(district|regional|administrative|boundary|terrain)\s+map\b", "scenic landscape illustration", new_prompt, flags=re.I)
        new_prompt = re.sub(r"\bmap\s+(highlighting|showing|of)\b", r"scenic illustration \1", new_prompt, flags=re.I)
        new_prompt = re.sub(r"\bhighlighting\s+([a-zA-Z\s]+)\s+constituencies\b", "showing scenic views of the region", new_prompt, flags=re.I)
        new_prompt = re.sub(r"\bhighlighting\s+([a-zA-Z\s]+)\s+boundaries\b", "showing the majestic landscape", new_prompt, flags=re.I)
        new_prompt += " CRITICAL: Pure physical landscape, landmark, or cultural illustration only. Absolutely NO map, NO boundary lines, NO place names, NO text anywhere."
    return new_prompt
