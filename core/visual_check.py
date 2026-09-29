"""Look at what Flow drew before it is published.

29 Sep: the French Guiana reel showed India's State Emblem with a garbled motto,
rupee notes and an Indian passport while the narrator talked about France.
Nothing between Flow and the upload ever looked at the picture. This does:
three frames of each raw clip (before our labels/locator are overlaid) go to a
Gemini vision model together with the line spoken over them, and it reports
anything a viewer would call wrong.

Blocking kinds: emblem (national emblem, state seal, coat of arms, crest,
official stamp), document (passport, ID, certificate, official paper), money
(banknotes or coins), wrong_country (a symbol, flag, currency or landmark of a
country the line is not about), contradiction (the picture shows something the
line does not say, or the opposite of it), text (readable or garbled writing
that is large enough to notice).
"""
import json
import os
import re
import subprocess
from pathlib import Path

MODEL = os.getenv("VISION_CHECK_MODEL", "gemini-3.5-flash-lite")
BLOCKING = {"emblem", "document", "money", "wrong_country", "contradiction", "text"}

PROMPT = """You check one short clip of a Hindi geography video before it is published.
You see {n} frames taken across the clip. The video is about: {topic}
The narrator says over this clip (Hindi): "{line}"

Report ONLY real problems a viewer would notice:
- emblem: any national emblem, state emblem, lion capital, seal, coat of arms, government crest or official stamp
- document: a passport, ID card, certificate, visa or other official paper
- money: banknotes or coins of any country
- wrong_country: a flag, symbol, currency, landmark or map of a country or place the line and the topic are NOT about (e.g. Indian symbols in a clip about France)
- contradiction: the picture clearly shows something the line does not say, or the opposite of it
- text: readable or garbled letters, words or numbers that are large enough to notice (ignore tiny unreadable specks)

A plain map, landscape, people, animals, buildings or a correct flag of the place the line names are fine.
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
            "Show no emblems, seals, crests, passports, documents, banknotes, coins or writing.")
