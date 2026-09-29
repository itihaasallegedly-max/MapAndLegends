"""Prompt 2 — script generation.

There is no template fallback in this module any more. If the model cannot
be reached, or returns something that does not satisfy the contract below,
this raises. A run that cannot write a real script must not produce a video.
"""
import json
import math
import os
import re
import subprocess

from dotenv import load_dotenv
from google import genai
from google.genai import types

from core import model_client, sound_map, narration_map, source_facts
from pipeline_errors import ConfigError, QuotaExhaustedError, ScriptGenerationError
from utils.frame_extractor import extract_frames_base64

load_dotenv()

# The target window, and the validator's tolerance around it. A set topic —
# eight dances, eight states — cannot be told in 50 seconds; the competitor's
# version of exactly that runs 100. Override per run:
#   RUNTIME_TARGET_MIN_S=80 RUNTIME_TARGET_MAX_S=100 ./venv/bin/python pipeline_daily.py ...
#
# Length follows the competitor video being remade. The only hard ceiling is the
# platform limit: YouTube Shorts and Instagram Reels both accept uploads up to
# 3 minutes (180 s). A competitor video longer than that is still remade, but
# the script is capped so the reel qualifies as a Short/Reel.
WORDS_PER_S = 2.3            # Flow's Hindi read, measured: 109 words -> 47.7s
MATCH_BAND = 0.08            # aim within +-8% of their runtime
SHORT_TOLERANCE = 0.85       # rejected only if under 85% of theirs (items dropped)
# Hard ceiling for 9:16 uploads: YouTube Shorts = 3 min, Instagram Reels = 3 min
PLATFORM_CAP_S = int(os.getenv("PLATFORM_CAP_S", "180"))


def _probe_seconds(path):
    if not path:
        return 0.0
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out or 0)
    except Exception:
        return 0.0


def runtime_window(competitor_s):
    """(low, high) seconds to aim for, or None when there is nothing to match.

    The high end is always capped at PLATFORM_CAP_S so the reel qualifies as a
    YouTube Short / Instagram Reel regardless of how long the competitor runs.
    RUNTIME_TARGET_MIN_S / RUNTIME_TARGET_MAX_S still override for one run.
    """
    lo, hi = os.getenv("RUNTIME_TARGET_MIN_S"), os.getenv("RUNTIME_TARGET_MAX_S")
    if lo and hi:
        return float(lo), min(float(hi), PLATFORM_CAP_S)
    if competitor_s and competitor_s > 0:
        raw_lo = competitor_s * (1 - MATCH_BAND)
        raw_hi = competitor_s * (1 + MATCH_BAND)
        hi_capped = min(raw_hi, PLATFORM_CAP_S)
        # If the competitor itself is longer than the cap, aim for the cap window
        lo_capped = min(raw_lo, PLATFORM_CAP_S)
        return lo_capped, hi_capped
    return None
REQUIRED_KEYS = ("title", "segments", "caption", "facts")

# Narration language. The competitor reels are in English; with "hi" the model
# takes their content, converts it and WRITES the narration ("text", "hook")
# directly in spoken Hindi. Everything else stays English: visual prompts,
# audio notes, facts (so they stay checkable), caption and upload metadata.
NARRATION_LANG = os.getenv("NARRATION_LANG", "hi").strip().lower()
DEVANAGARI = re.compile(r"[\u0900-\u097F]")

HINDI_BRIEF = """
    NARRATION LANGUAGE: HINDI.
    The competitor video and any source material are in English. Understand the
    content, then WRITE the narration yourself in natural spoken Hindi — do not
    translate line by line. Every segment's "text" must be in
    Hindi, in Devanagari script:
    - Everyday Hindustani, the way a warm Indian documentary narrator speaks on
      YouTube — not heavy Sanskritised Hindi, not Hinglish in Roman letters.
    - Place, river, state and country names as Indians say them in Hindi
      ("ओडिशा", "ब्रह्मपुत्र", "अरुणाचल प्रदेश").
    - Write every number as Hindi words ("चौदह सौ पचास किलोमीटर"), never digits.
    - The word limits below apply to the Hindi words you write.
    - Keep "video_prompt", "per_second", "background", "audio", "facts"
      and "upload_hashtags" in ENGLISH.
    - "caption" and "upload_title" are in HINDI (Devanagari) — they are what
      viewers read on YouTube and Instagram, so they match the narration.
      Hashtags inside the caption stay in English letters (#Geography).
    - "title_regional" is the title in Hindi (Devanagari).
"""


# NARRATION_MODE=faithful (default 27-29 Sep 2026, now opt-in): the competitor's narration is
# transcribed line by line (core/narration_map.py) and TRANSLATED into Hindi —
# same lines, same order, nothing added or dropped. Our own story rules (hook,
# linking words, payoff) do not apply then: their script is the script.
# NARRATION_MODE=free (default since 29 Sep 2026): the model writes its own story.
# NARRATION_MODE=rewrite (29 Sep 2026): their video is transcribed, boiled down
# to bare fact notes (core/source_facts.py), and the script is written fresh
# from those notes WITHOUT seeing their video; an originality pass then checks
# it against their transcript. Their facts, our words, order and visuals.
NARRATION_MODE = os.getenv("NARRATION_MODE", "free").strip().lower()
MAX_DROPPED_LINES = 0.10     # on the last try, accept at most 10% of their lines missing

HINDI_FAITHFUL_BRIEF = HINDI_BRIEF.replace(
    """Understand the
    content, then WRITE the narration yourself in natural spoken Hindi — do not
    translate line by line.""",
    """Their narration is
    transcribed below, line by line. TRANSLATE it into natural spoken Hindi —
    fluent, the way a Hindi narrator would say it, but every line and every
    piece of information kept, in their order.""") + """
    SPOKEN HINDI, NOT BOOK HINDI. Write it the way a popular Hindi YouTuber
    talks to a friend:
    - Everyday words over Sanskritised ones: कहानी not किंवदंती, बॉर्डर/सीमा
      not ज़मीनी सीमा साझा करना, आखिर में not अंततः, पक्का/ठीक-ठीक not सटीक,
      बाँटना not विभाजित करना, हिस्सा not भाग. Common English words Indians say
      are fine: बॉर्डर, आइलैंड, वाइन, जिन, लेजेंड, ट्रीटी, रिएक्शन.
    - Translate the MEANING correctly: "Kingdom of the Netherlands" is
      नीदरलैंड्स का किंगडम / नीदरलैंड्स (not साम्राज्य = empire); "wine" is
      वाइन (not शराब = any alcohol); "walked faster" is तेज़ चला (not
      तेज़ी से पैदल यात्रा की).
    - Short and tight: say each thing once, drop filler (कि, जो, वह, था/थी
      where the sentence works without them). The Hindi must be spoken in about
      the same time as their English, so it has to be SHORTER in words than a
      literal translation — cut words, never facts.
"""

FLOW_HINDI_WPS = float(os.getenv("FLOW_HINDI_WPS", "2.75"))   # measured 29 Sep: 294 words -> 106.8s

STORY_RETRIES = int(os.getenv("PROMPT2_STORY_RETRIES", "3"))
STORY_BRIEF = """
    THE NARRATION IS ONE STORY, NOT A LIST OF FACTS.
    Each scene's line is voiced separately, clip by clip, so the only thing that
    carries the viewer from one clip to the next is the words. Write it so that
    a viewer who hears only the narration follows one thread from start to end:
    - Every line after the first picks up from the line before it. Start it with
      a linking word or a reference back ("लेकिन", "फिर", "इसीलिए", "इसी वजह से",
      "तो", "इसके बाद", "जवाब में", "तब", "यानी", "आखिर में", "यह", "इसी",
      "दोनों", ...). No line may start a new subject cold.
    - Cause before effect. If a line says something happened (a coup, a war, a
      partition, a treaty), that line or the one before it says who did it and
      why. "After a coup, Turkey occupied the north" without whose coup and
      what it wanted is a fact, not a story.
    - Chronology runs one way. If you jump back in time, say so
      ("यह कहानी शुरू होती है ..."), then move forward without skipping back.
    - One or two complete sentences per scene. Never split a sentence across
      two scenes: each clip is voiced on its own and a half-sentence cannot be.
"""

# Words a Hindi line uses to pick up from the one before, matched as whole
# words in its first four (prefix matching let "उन्नीस" pass as "उन").
LINKERS = set("""
लेकिन पर परंतु मगर किंतु फिर इसीलिए इसलिए इसी इस इसे इसका इसकी इसके इसने इन इन्हें इनका इनकी इनके
तो तब जब जवाब यानी आखिर आख़िर और क्योंकि यह ये वह वो वे उस उसे उसी उसका उसकी उसके उसने उन उन्हें
उनका उनकी उनके उन्होंने दोनों जिसके जिसकी जिससे नतीजा नतीजतन अब आज बाद यहीं वहीं यहाँ वहाँ वहां यहां
पहला पहली दूसरा दूसरी तीसरा तीसरी चौथा चौथी पाँचवाँ पांचवां बाकी बाक़ी साथ कहानी हालांकि हालाँकि इसीलिये
""".split())
COUNT_WORDS = {"दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6, "सात": 7}
COUNT_NOUNS = ("हिस्स", "टुकड़", "टुकड", "भाग", "कारण", "वजह", "राज", "रहस्य", "देश", "नदि", "नदी",
               "शहर", "सवाल", "चीज़", "चीज", "बात", "तरीक")
ORDINALS = [("पहला", "पहली", "पहले"), ("दूसरा", "दूसरी", "दूसरे"), ("तीसरा", "तीसरी", "तीसरे"),
            ("चौथा", "चौथी", "चौथे"), ("पाँचवाँ", "पांचवां", "पाँचवीं", "पांचवीं"), ("छठा", "छठी"),
            ("सातवाँ", "सातवां", "सातवीं")]


def _norm(t):
    return re.sub(r"[\s\u0964\u0965!?,.:;\"'“”‘’—–-]+", " ", str(t)).strip()


def check_story(segments, hook=None):
    """The narration must read as one thread. Raises with what to fix."""
    if NARRATION_LANG != "hi" or len(segments) < 3:
        return
    cold = [i for i, sg in enumerate(segments[1:], 2)
            if not LINKERS.intersection(_norm(sg.get("text", "")).split()[:4])]
    allowed = (len(segments) - 1) // 4          # one in four may link by content alone
    if len(cold) > allowed:
        raise ScriptGenerationError(
            f"Scenes {cold} start a new subject cold. Each line after the first must pick up "
            f"from the one before: open it with a linking word (लेकिन, फिर, इसीलिए, तो, "
            f"इसके बाद, जवाब में, यानी, आखिर में ...) or refer back to what was just said.")

def _extract_json(raw):
    """Pull the JSON object out of a model response.

    Handles bare JSON, fenced blocks, and leading prose. Raises rather than
    guessing if there is no object in there at all.
    """
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ScriptGenerationError(
            f"No JSON object in model response (first 300 chars): {raw[:300]!r}"
        )
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        first = e
    # Flash-lite regularly leaves a trailing comma, a // note or a raw newline
    # inside a string. Those are syntax slips, not a bad script: repair and
    # re-parse before spending another Gemini call on the same topic.
    try:
        data = json.loads(_repair_json(text[start:]))
        print(f"[Prompt 2] repaired malformed JSON ({first.msg} at line {first.lineno} col {first.colno})")
        return data
    except json.JSONDecodeError:
        pass
    _dump_raw(raw, first)
    raise ScriptGenerationError(
        f"Model returned malformed JSON ({first}): {text[start:start + 300]!r}"
    ) from first


def _repair_json(s):
    """String-aware fixes: drop trailing commas and // comments, escape raw
    newlines/tabs inside strings, close whatever a truncated reply left open."""
    out, stack = [], []
    in_str = esc = False
    i, n = 0, len(s)

    def drop_trailing_comma():
        j = len(out) - 1
        while j >= 0 and out[j] in " \t\r\n":
            j -= 1
        if j >= 0 and out[j] == ",":
            del out[j]

    while i < n:
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch == "\n":
                ch = "\\n"
            elif ch == "\t":
                ch = "\\t"
            out.append(ch)
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif (ch.isalpha() or ch == "_") and _prev_sig(out) in "{,":
            # bare key like  _per_second: [  -> "per_second": [
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", s[i:])
            if m:
                out.append('"' + m.group(1).lstrip("_") + '":')
                i += m.end()
                continue
            out.append(ch)
        elif ch == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] != "\n":
                i += 1
            continue
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            out.append(ch)
        elif ch in "}]":
            drop_trailing_comma()
            if stack:
                stack.pop()
            out.append(ch)
            if not stack:
                break
        else:
            out.append(ch)
        i += 1
    if in_str:
        out.append('"')
    drop_trailing_comma()
    while stack:
        out.append(stack.pop())
    return "".join(out)


def _prev_sig(out):
    for c in reversed(out):
        if c not in " \t\r\n":
            return c
    return ""


def _dump_raw(raw, err):
    """Keep the reply that failed, so the next fix is made from evidence."""
    try:
        import datetime
        logs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(logs, exist_ok=True)
        path = os.path.join(logs, f"prompt2_bad_json_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {err}\n{raw}")
        print(f"[Prompt 2] raw reply saved to {path}")
    except OSError:
        pass


# ------------------------------------------------------------ mid-roll CTA
# The ask sits INSIDE an existing scene, after a finished sentence, around the
# middle of the reel: no extra clip, no hard cut to a branding card, and at
# most one step (usually +2s) on that one scene. The model is asked for it in
# the prompt too, but a faithful translation leaves no room for it, so it is
# placed here when the model did not.
CTA_HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "cta_history.txt")


def _recent_ctas(n=15):
    try:
        with open(CTA_HISTORY, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()][-n:]
    except OSError:
        return []


def _recent_cta_note():
    recent = _recent_ctas()
    if not recent:
        return ""
    return (" Recent videos already used these — do not repeat or closely echo any of them: "
            + " | ".join(recent))


def _valid_cta(line, recent):
    line = " ".join(str(line or "").split())
    if not line or not re.search(r"[\u0900-\u097F]", line):
        return None, "not Hindi"
    if not _CTA_WORDS.search(line):
        return None, "does not ask to subscribe"
    if len(line.split()) > 12:
        return None, f"{len(line.split())} words, max 10"
    norm = lambda x: re.sub(r"[^\w\u0900-\u097F]", "", x)
    if any(norm(line) == norm(r) for r in recent):
        return None, "same as a recent video's line"
    if not re.search(r"[।.!?]$", line):
        line += "।"
    return line, None
_CTA_WORDS = re.compile(r"सब्सक्राइब|subscribe|Subscribe", re.I)


def write_midroll_cta(client, types, data, tries=3):
    """One short, fresh Hindi subscribe line for this script, from the model."""
    recent = _recent_ctas(50)
    segs = data.get("segments") or []
    story = "\n".join(f"{i + 1}. {sg.get('text', '')}" for i, sg in enumerate(segs))
    why = ""
    for _ in range(tries):
        prompt = (
            "This is the Hindi narration of a short geography video for the channel "
            "मैप एंड लेजेंड, scene by scene:\n" + story + "\n\n"
            "Write ONE line the narrator says around the middle, asking viewers to subscribe. "
            "Everyday spoken Hindi in Devanagari, at most 10 words, must contain the word "
            "सब्सक्राइब. Make it belong to THIS story: pick up from what was just revealed "
            "and lean into what comes next, so it sounds like part of the narration, not an ad. "
            "No stock phrases."
            + (" Do not repeat or echo any of these recent lines: " + " | ".join(recent[-15:]) if recent else "")
            + (f" Your last answer was rejected: {why}." if why else "")
            + ' Return JSON only: {"midroll_cta": "<line>", "midroll_after": <scene number it follows>}')
        try:
            resp, _ = model_client.call_model(
                lambda m: client.models.generate_content(
                    model=m, contents=[prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")),
                models=["gemini-3.5-flash-lite"], what="mid-roll CTA line")
            out = _extract_json(resp.text)
        except QuotaExhaustedError:
            raise
        except Exception as e:  # noqa: BLE001 — optional line, never costs the reel
            print(f"[CTA] line request failed: {type(e).__name__}: {e}")
            return None
        line, why = _valid_cta(out.get("midroll_cta"), recent)
        if line:
            if out.get("midroll_after"):
                data["midroll_after"] = out["midroll_after"]
            return line
    return None


def _remember_cta(line):
    try:
        os.makedirs(os.path.dirname(CTA_HISTORY), exist_ok=True)
        with open(CTA_HISTORY, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def insert_midroll_cta(data):
    """Append one short subscribe line to a mid-reel scene. Returns the index or None."""
    if os.getenv("MIDROLL_CTA", "1") == "0" or NARRATION_LANG != "hi":
        return None
    segs = data.get("segments") or []
    if len(segs) < 4:
        return None
    total = sum(float(sg.get("seconds") or 0) for sg in segs)
    t, starts = 0.0, []
    for sg in segs:
        starts.append(t)
        t += float(sg.get("seconds") or 0)
    # already there (the model wrote one) anywhere but the last scene
    for i, sg in enumerate(segs[:-1]):
        if _CTA_WORDS.search(str(sg.get("text", ""))):
            print(f"[CTA] mid-roll already in scene {i + 1}")
            return i
    line, why = _valid_cta(data.get("midroll_cta"), _recent_ctas(50))
    if not line:
        print(f"[CTA] model's mid-roll line unusable ({why}) — none this time")
        return None
    n_cta = len(line.split())
    try:
        want = int(data.get("midroll_after") or 0) - 1      # the model's own pick, 0-based
    except (TypeError, ValueError):
        want = -1
    # The line was written to follow the model's chosen scene, so it goes
    # there whenever that scene is anywhere reasonable (20-85% in).
    band = (0.35, 0.7)
    if 0 < want < len(segs) - 1:
        w_end = starts[want] + float(segs[want].get("seconds") or 0)
        w_txt = str(segs[want].get("text", "")).rstrip()
        if 0.2 * total <= w_end <= 0.85 * total and not w_txt.endswith(("...", "…", "?")):
            band = (0.0, 1.0)
        else:
            want = -1
    best = None
    for i, sg in enumerate(segs):
        end = starts[i] + float(sg.get("seconds") or 0)
        if want >= 0 and i != want:
            continue
        if i in (0, len(segs) - 1) or sg.get("_cta") or not (band[0] * total <= end <= band[1] * total):
            continue
        text = str(sg.get("text", "")).rstrip()
        if text.endswith(("...", "…")) or text.endswith("?"):
            continue                      # a joke setup or a question: the next beat answers it
        words = len(text.split()) + n_cta
        sec = float(sg.get("seconds") or 0)
        need = next((L for L in _PACE_LENGTHS if L >= sec and words / L <= min(MAX_WPS, FLOW_HINDI_WPS + 0.3)), None)
        if need is None:
            continue
        added = need - sec
        # the model's chosen scene first (it wrote the line to follow it),
        # then no extra seconds, then nearest the middle
        score = (i != want, added, abs(end - 0.5 * total))
        if best is None or score < best[0]:
            best = (score, i, need)
    if best is None:
        # Every mid scene is full: give the line its own short scene that
        # CONTINUES the previous shot (same picture, camera keeps drifting),
        # so it plays as a breath in the story, not a cut to an ad.
        mids = [want] if want >= 0 else [
                i for i in range(1, len(segs) - 1)
                if 0.35 * total <= starts[i] + float(segs[i].get("seconds") or 0) <= 0.7 * total
                and not str(segs[i].get("text", "")).rstrip().endswith(("...", "…", "?"))]
        if not mids:
            print("[CTA] no mid-reel break for the subscribe line — skipped")
            return None
        i = min(mids, key=lambda k: abs(starts[k] + float(segs[k].get("seconds") or 0) - 0.5 * total))
        L = 4 if n_cta / 4 <= FLOW_HINDI_WPS + 0.3 else 6
        if total + L > PLATFORM_CAP_S:
            print("[CTA] skipped: would pass the platform limit")
            return None
        prev = segs[i]
        last_beat = (prev.get("per_second") or ["the same shot"])[-1]
        new_seg = {
            "text": line, "speaker": "NARRATOR (V.O.)", "seconds": float(L),
            "narration_refs": [], "labels": [], "_midroll": True,
            "video_prompt": (str(prev.get("video_prompt") or "").rstrip(".")
                             + ". The same shot continues: the camera keeps drifting slowly, nothing new appears."),
            "per_second": [f"continuing from: {last_beat}; slow drift"] * L,
            "background": prev.get("background") or {},
            "audio": {"ambience": (prev.get("audio") or {}).get("ambience", ""), "sfx": [],
                      "voice": "warm, direct to the viewer, then straight back into the story"},
        }
        segs.insert(i + 1, new_seg)
        data["segments"] = segs
        _remember_cta(line)
        print(f"[CTA] mid-roll as its own {L}s scene after scene {i + 1} "
              f"(at ~{starts[i] + float(prev.get('seconds') or 0):.0f}s of {total + L:.0f}s): {line}")
        return i + 1
    (_, added, _), i, need = best
    if total + added > PLATFORM_CAP_S:
        print("[CTA] skipped: would pass the platform limit")
        return None
    sg = segs[i]
    sg["text"] = str(sg["text"]).rstrip() + " " + line
    if need != sg.get("seconds"):
        sg["seconds"] = float(need)
        if sg.get("per_second"):
            sg["per_second"] = _resize_beats(sg["per_second"], int(need))
    _remember_cta(line)
    print(f"[CTA] mid-roll added to scene {i + 1} of {len(segs)} "
          f"(+{added:.0f}s, at ~{starts[i]:.0f}s of {total + added:.0f}s): {line}")
    return i


_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _nums(text):
    """Numbers in a string, commas dropped: '2,500' -> '2500', '21.5' kept."""
    return {n.replace(",", "") for n in _NUM_RE.findall(str(text).translate(_DEVA_DIGITS))}


_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def latin_in_text(segments):
    """[(scene index, [words])] where the spoken line uses English letters."""
    out = []
    for i, sg in enumerate(segments):
        ws = _LATIN_WORD.findall(str(sg.get("text", "")))
        if ws:
            out.append((i, ws[:6]))
    return out


_FILLER_LABEL = re.compile(r"^\s*(\d+)\s+(dishes|items|places|foods|things|facts|rivers|cities|spots|sites|states|districts)\s*$", re.I)


def drop_filler_labels(segments, nm):
    """Remove '4 Dishes'-style counts the narrator never says."""
    said = set()
    for ln in nm.get("lines") or []:
        said |= _nums(ln.get("text", ""))
    for sg in segments:
        kept = []
        for lb in sg.get("labels") or []:
            m = _FILLER_LABEL.match(str(lb))
            if m and m.group(1) not in said:
                print(f"[Labels] dropped filler label {lb!r}")
                continue
            kept.append(lb)
        if "labels" in sg:
            sg["labels"] = kept


def missing_numbers(segments, nm):
    """[(line id, [numbers])] for competitor numbers no scene carries as digits.
    Checked against labels + text of all scenes, not just the referencing one:
    a number moved one scene over is fine, a number gone is not."""
    have = set()
    for sg in segments:
        have |= _nums(sg.get("text", ""))
        for lb in sg.get("labels") or []:
            have |= _nums(lb)
    out = []
    for ln in nm.get("lines") or []:
        want = sorted(_nums(ln.get("text", "")) - have)
        if want:
            out.append((ln.get("id", "?"), want))
    return out


MAX_WPS = 3.4
_PACE_LENGTHS = (4, 6, 8, 10)


def _fit_len(words):
    """Smallest valid scene length that carries `words` at <= MAX_WPS, or None."""
    return next((L for L in _PACE_LENGTHS if words / L <= MAX_WPS), None)


def _resize_beats(beats, n):
    beats = list(beats or [])
    if not beats:
        return beats
    while len(beats) < n:
        beats.append(beats[-1])
    return beats[:n]


def _split_text(text):
    """Split at the sentence or clause boundary nearest the middle."""
    words = text.split()
    mid = len(words) / 2
    best = None
    for strong in (True, False):
        marks = "।?!.\u0965" if strong else ",;:—-"
        for k in range(1, len(words)):
            if words[k - 1][-1:] in marks:
                if best is None or abs(k - mid) < abs(best - mid):
                    best = k
        if best is not None:
            break
    if best is None:
        best = int(round(mid))
    return " ".join(words[:best]), " ".join(words[best:])


def _repair_pace(segments, depth=0):
    out = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        words = len(text.split())
        sec = float(seg.get("seconds") or 0)
        if sec <= 0 or words / sec <= MAX_WPS:
            out.append(seg)
            continue
        L = _fit_len(words)
        if L is not None:
            print(f"[Pace] {words} words in {sec:.0f}s ({words/sec:.1f} w/s) → scene lengthened to {L}s")
            seg = dict(seg)
            seg["seconds"] = float(L)
            if seg.get("per_second"):
                seg["per_second"] = _resize_beats(seg["per_second"], L)
            out.append(seg)
            continue
        if depth >= 3:
            raise ScriptGenerationError(
                f"Segment is too fast even after splitting ({words} words). "
                f"Break it down into shorter sentences.")
        a_txt, b_txt = _split_text(text)
        parts = []
        beats = list(seg.get("per_second") or [])
        used = 0
        for t in (a_txt, b_txt):
            n = _fit_len(len(t.split())) or 10
            part = dict(seg)
            part["text"] = t
            part["seconds"] = float(n)
            if beats:
                part["per_second"] = _resize_beats(beats[used:] or beats[-1:], n)
                used = min(used + n, len(beats))
            parts.append(part)
        print(f"[Pace] {words} words would not fit a 10s scene → split into "
              f"{parts[0]['seconds']:.0f}s + {parts[1]['seconds']:.0f}s")
        out.extend(_repair_pace(parts, depth + 1))
    return out


def validate_script(data, topic_spec, window=None, faithful=False):
    """Enforce the contract. Returns the script; raises on any violation."""
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ScriptGenerationError(f"Script missing required keys: {missing}")

    segments = data["segments"]
    if not isinstance(segments, list) or len(segments) < 3:
        raise ScriptGenerationError(
            f"Expected at least 3 segments, got {len(segments) if isinstance(segments, list) else type(segments).__name__}"
        )
    segments = list(segments)
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or not str(seg.get("text", "")).strip():
            raise ScriptGenerationError(f"Segment {i} has no spoken text: {seg!r}")
        # `image_prompt` and `visual` are older field names; accept both so
        # scripts written before the move to text-to-video still load.
        prompt_text = str(seg.get("video_prompt") or seg.get("image_prompt")
                          or seg.get("visual") or "").strip()
        if not prompt_text:
            raise ScriptGenerationError(
                f"Segment {i} has no video_prompt — the script sheet needs one "
                f"shot per scene: {seg!r}"
            )
        seg["video_prompt"] = prompt_text
        try:
            seg["seconds"] = float(seg.get("seconds", 0))
        except (TypeError, ValueError):
            raise ScriptGenerationError(f"Segment {i} has a non-numeric 'seconds': {seg!r}")
        
    # Pace: a segment over MAX_WPS used to reject the whole script, and a
    # 3.5 w/s line cost a full Gemini round-trip (5 of those = no reel that
    # day, 28 & 29 Sep). Now it is repaired in place: lengthen the scene to
    # the next 4/6/8/10 that fits, and only if even 10 s is too short, split
    # the line at a sentence/clause boundary into two scenes.
    segments = _repair_pace(segments)
    data["segments"] = segments

    # The scene grammar: 4/6/8/10 seconds a scene, one prompt per second, a
    # full audio description per scene. A model that answered with 15-second
    # scenes, or left `per_second` out, is corrected here rather than failing
    # the run — the correction only ever rewrites timing and camera language.
    subject_name = topic_spec.get("subject") or topic_spec.get("topic", "")
    # (Previously scene_grammar.apply was called here. Now we just trust the model's segments)
    data["segments"] = segments

    # --- pass 1: strip the competitor's own CTA line -------------------------
    # The competitor's last line is often their own "like, share and subscribe"
    # ask. We translate it faithfully but then replace it with our own CTA
    # variant (appended by maybe_append_cta). Detect it by keywords.
    _CTA_KEYWORDS = re.compile(
        r"(like|share|subscribe|सब्सक्राइब|लाइक|शेयर|comment|कमेंट)",
        re.IGNORECASE)
    segs = data["segments"]
    if segs and _CTA_KEYWORDS.search(str(segs[-1].get("text", ""))):
        dropped = segs.pop()
        print(f"[Script] stripped competitor CTA line from last segment: "
              f"{str(dropped.get('text',''))[:80]!r}")
        if segs and dropped.get("narration_refs"):
            segs[-1].setdefault("narration_refs", []).extend(dropped["narration_refs"])
        data["segments"] = segs
        segments = segs

    # --- pass 2: merge dangling mid-sentence fragments -----------------------
    # A segment whose text ends without sentence-closing punctuation (।, ?, !,
    # ., …) is a mid-sentence fragment — voiced as a hanging clause with no
    # full stop. Merge it with the next segment if the combined scene duration
    # fits inside a valid Flow clip length (≤10 s). This eliminates the choppy
    # joins that produce continuity gaps in the stitched reel.
    SENTENCE_END = re.compile(r"[।?!.…।\u0964\u0965]\s*$")
    VALID_LENGTHS = (4, 6, 8, 10)

    def _merge_segments(a, b):
        """Combine two segments: join text, sum seconds (capped at 10), merge beats."""
        combined_s = a["seconds"] + b["seconds"]
        # snap to the next valid clip length ≥ combined
        snapped = next((v for v in VALID_LENGTHS if v >= combined_s), 10)
        merged = dict(b)          # carry b's video/audio metadata as the main scene
        merged["text"] = str(a.get("text", "")).rstrip() + " " + str(b.get("text", "")).lstrip()
        merged["seconds"] = float(snapped)
        # combine per-second beats proportionally (keep b's if merging would overflow)
        a_beats = list(a.get("per_second") or [])
        b_beats = list(b.get("per_second") or [])
        if a_beats and b_beats:
            merged["per_second"] = (a_beats + b_beats)[:snapped]
        return merged

    merged_segs, i = [], 0
    while i < len(segments):
        seg = segments[i]
        text = str(seg.get("text", "")).rstrip()
        combined_s = seg["seconds"]
        # Keep merging forward as long as this segment is an open fragment AND
        # the next one exists AND the combined duration fits ≤ 10 s
        while (not SENTENCE_END.search(text)
               and i + 1 < len(segments)
               and combined_s + segments[i + 1]["seconds"] <= 10):
            i += 1
            seg = _merge_segments(seg, segments[i])
            text = str(seg.get("text", "")).rstrip()
            combined_s = seg["seconds"]
            print(f"[Script] merged dangling fragment → {seg['seconds']:.0f}s: "
                  f"{str(seg.get('text',''))[:80]!r}")
        merged_segs.append(seg)
        i += 1

    if len(merged_segs) != len(segments):
        print(f"[Script] fragment merge: {len(segments)} → {len(merged_segs)} segments")
        data["segments"] = merged_segs
        segments = merged_segs

    for seg in segments:
        text = str(seg.get("text", "")).rstrip()
        if not SENTENCE_END.search(text):
            seg["text"] = re.sub(r"[,;:-]\s*$", "", text) + "।"

    if not faithful:
        check_story(segments)

    if NARRATION_LANG == "hi":
        roman = [i for i, sg in enumerate(segments) if not DEVANAGARI.search(str(sg.get("text", "")))]
        if roman:
            raise ScriptGenerationError(
                f"Narration must be Hindi in Devanagari; segment(s) {roman} are not. "
                f"Write every segment's text in Hindi.")
        data["narration_lang"] = "hi"

    requested_video_s = sum(s["seconds"] for s in segments)
    
    # Estimate actual audio duration from the narration text
    from utils.audio_estimation import estimate_audio_duration
    estimated_audio_s = sum(estimate_audio_duration(s.get("text", "")) for s in segments)
    total_words = sum(len(str(s.get("text", "")).split()) for s in segments)
    
    # Too-short check: a remake that runs well short of the competitor has dropped content.
    if window and not faithful and estimated_audio_s < window[0] * SHORT_TOLERANCE:
        need = int(window[0] * WORDS_PER_S)
        raise ScriptGenerationError(
            f"Script is too short for the competitor video it remakes: estimated audio "
            f"{estimated_audio_s:.1f}s ({total_words} words), theirs needs about "
            f"{window[0]:.0f}-{window[1]:.0f}s — write about {need}+ words and cover every "
            f"item and beat they cover, adding scenes as needed."
        )

    # Too-long check: 9:16 uploads must not exceed the platform limit.
    # YouTube Shorts = 3 min, Instagram Reels (upload) = 3 min → PLATFORM_CAP_S = 180 s.
    if requested_video_s > PLATFORM_CAP_S:
        max_words = int(PLATFORM_CAP_S * WORDS_PER_S)
        raise ScriptGenerationError(
            f"Script is too long for a 9:16 Short/Reel: video segments total "
            f"{requested_video_s:.0f}s but the platform limit is {PLATFORM_CAP_S}s (3 minutes). "
            f"Cut scenes so the total is ≤{PLATFORM_CAP_S}s (≤{max_words} narration words). "
            f"Prioritise the most impactful beats; trim or merge the rest."
        )

    data["estimated_audio_s"] = round(estimated_audio_s, 2)
    data["requested_video_s"] = requested_video_s

    facts = data["facts"]
    if not isinstance(facts, list) or not facts:
        raise ScriptGenerationError("Script returned an empty 'facts' array — nothing to fact-check")
    data["facts"] = [str(f).strip() for f in facts if str(f).strip()]
    if not data["facts"]:
        raise ScriptGenerationError("Every entry in 'facts' was blank")

    if not str(data["title"]).strip():
        raise ScriptGenerationError("Script returned a blank title")

    if topic_spec.get("kind") == "set":
        validate_set(data, topic_spec)
    normalize_caption(data, topic_spec)
    # maybe_append_cta(data, topic_spec)

    return data


# ------------------------------------------------------------------ CTA segment
# A short, unique call-to-action appended to the reel whenever there is at
# least 6 seconds of headroom under the platform cap. Each variant is phrased
# differently so the audience never hears the same closing line twice. The
# {topic} placeholder is filled with the video title so the ask feels organic.
#
# To disable: set env  APPEND_CTA=0
# To pin a specific variant: set env  CTA_VARIANT=3  (0-indexed)

_CTA_POOL = [
    # variant 0 — question hook
    {
        "hi":  "क्या आपको यह सफर पसंद आया? Like करें, Comment में बताएं, और Subscribe करें — हर दिन नया Geography!",
        "en":  "Did you enjoy the journey? Hit Like, drop a comment, and Subscribe — new geography every single day!",
        "video_prompt": "The camera slowly pulls back from a glowing world map until the whole globe fills the frame, continents shimmering gold. A soft animated pulse ripples outward from the subject region.",
        "beat": ["Glowing world map fills frame, camera pulling back",
                 "Continents shimmer, subject region pulses gold",
                 "Subscribe bell icon fades in bottom-right, glowing",
                 "Bell rings with a soft chime, light ripple outward",
                 "Map rotates gently, new pin drops on another region",
                 "Screen brightens to white, channel name lingers"],
    },
    # variant 1 — map-explorer framing
    {
        "hi":  "दुनिया बड़ी है — Subscribe करें ताकि एक भी नक्शा छूट न जाए। Like और Comment ज़रूर करें!",
        "en":  "The world is vast — Subscribe so you never miss a map. Drop a Like and tell us what to explore next!",
        "video_prompt": "An animated compass spins on a vintage parchment map; the needle settles on a new destination as a dotted trail traces across the continents.",
        "beat": ["Vintage parchment map, compass spinning in centre",
                 "Needle slows, dotted trail appears from subject region",
                 "Trail curves across continents toward a new pin",
                 "Pin drops with a soft thud, location circle expands",
                 "Subscribe text rises from the circle in bold serif",
                 "Compass needle points at viewer, map fades to warmth"],
    },
    # variant 2 — curiosity tease
    {
        "hi":  "अगला रहस्य जानना है? Subscribe करें, Like करें, और Comment में लिखें — कहाँ जाएं अगली बार?",
        "en":  "Want to know the next secret? Subscribe, hit Like, and comment where we should go next!",
        "video_prompt": "A dark map with glowing question marks scattered across it; one by one they pop open into colourful illustrated scenes as the camera drifts forward.",
        "beat": ["Dark map, glowing question marks scattered across it",
                 "First question mark bursts open — colourful illustration",
                 "Camera drifts forward, second mark pops open",
                 "Third mark opens — viewer's perspective descends in",
                 "Question mark in centre pulses, text fades in beneath",
                 "All marks glow together, warm light fills the frame"],
    },
    # variant 3 — community ask
    {
        "hi":  "आपकी favourite geography कौन सी है? Comment में बताएं, Like करें, और हमें Subscribe करना मत भूलें!",
        "en":  "What's your favourite geography fact? Comment below, hit Like, and don't forget to Subscribe!",
        "video_prompt": "Split-screen montage: four tiny landscapes (mountain, river delta, desert dune, island) arranged in a grid, each animated with a gentle zoom, against a dark atlas background.",
        "beat": ["Four landscape tiles appear on dark atlas background",
                 "Mountain tile zooms, snow cap glistens",
                 "River delta tile animates, waterways spread",
                 "Desert dune tile shifts in wind, ripple of sand",
                 "Island tile zooms, turquoise water glows",
                 "Tiles merge into one frame, Subscribe button pulses"],
    },
    # variant 4 — streak / habit framing
    {
        "hi":  "रोज़ एक नई दुनिया — Subscribe करें और Like करके हमें बताएं यह सफर जारी रखें!",
        "en":  "A new world every day — Subscribe and Like to keep this journey going!",
        "video_prompt": "A calendar-style grid of tiny globe icons, each lighting up in sequence like a streak tracker, ending with today's globe glowing brightest.",
        "beat": ["Calendar grid of tiny globe icons, all dim",
                 "First icon lights up gold, then second",
                 "Chain of lights races across the grid",
                 "Today's globe pulses largest, warmest glow",
                 "Streak counter ticks up beneath it",
                 "All icons glow together, Subscribe text rises"],
    },
    # variant 5 — record-breaking hook
    {
        "hi":  "क्या आप जानते थे यह? अगर नहीं, तो Subscribe करें — और Like ज़रूर करें ताकि और लोग जान सकें!",
        "en":  "Did you know that? If not, Subscribe — and Like so more people can discover it too!",
        "video_prompt": "A tall fact-card stack fans out on screen, each card a different geography highlight; the top card flips to reveal a glowing Like icon underneath.",
        "beat": ["Tall stack of fact cards, fanned out on screen",
                 "Cards fan wider, each shows a geography icon",
                 "Top card lifts and flips in slow motion",
                 "Underside reveals a glowing Like icon",
                 "Icon expands, ripple of light across all cards",
                 "Cards fold back; Subscribe text slides up centre"],
    },
    # variant 6 — explorer persona
    {
        "hi":  "हर नक्शे में एक कहानी है — Like करें, Subscribe करें, और अगले सफर के लिए तैयार रहें!",
        "en":  "Every map tells a story — Like, Subscribe, and get ready for the next adventure!",
        "video_prompt": "A worn leather explorer's journal opens; hand-drawn maps spill out, one page turning to show the subject region with a glowing red route traced across it.",
        "beat": ["Leather journal cover, candlelight ambience",
                 "Journal opens, hand-drawn maps fan out",
                 "Subject-region page turns to front",
                 "Red route traces itself across the map",
                 "Route completes, a new blank page turns",
                 "Blank page fills with Subscribe prompt, pen marks it"],
    },
    # variant 7 — gratitude close
    {
        "hi":  "देखने के लिए शुक्रिया! Like और Subscribe करें — आपका Support हमें आगे बढ़ाता है।",
        "en":  "Thanks for watching! Like and Subscribe — your support keeps us exploring.",
        "video_prompt": "Aerial shot pulling up from the subject landscape into open sky at golden hour; the horizon glows amber and the channel watermark fades in as a warm vignette closes.",
        "beat": ["Low aerial over subject landscape, golden hour light",
                 "Camera tilts up, sky fills more of frame",
                 "Horizon glows amber, clouds catch the light",
                 "Channel watermark fades in, centred on sky",
                 "Vignette darkens edges softly, warmth holds",
                 "Subscribe badge glows beneath watermark, gentle pulse"],
    },
    # variant 8 — rhetorical challenge
    {
        "hi":  "क्या आप अगला जवाब पहले जानना चाहते हैं? Subscribe करें! Like करके इसे और लोगों तक पहुंचाएं।",
        "en":  "Want the next answer first? Subscribe! Hit Like to share it with fellow explorers.",
        "video_prompt": "A radar sweep animation on a dark map; the beam circles and illuminates hidden geography icons — mountains, rivers, cities — each blinking to life as the sweep passes.",
        "beat": ["Dark map, radar sweep origin at subject region",
                 "First sweep arc, mountain icons blink on",
                 "Second sweep, river icons light up",
                 "Third sweep, city dots pulse into view",
                 "Full map glows with discovered icons",
                 "Radar origin pulses Subscribe beacon, warm ring expands"],
    },
]

_CTA_DURATION_S = 6   # always a 6-second scene (shortest valid Flow clip)


def _pick_cta_variant(data):
    """Pick a CTA variant deterministically per-video (title hash) so reruns are stable."""
    import hashlib
    title = str(data.get("title") or data.get("upload_title") or "")
    idx_env = os.getenv("CTA_VARIANT", "").strip()
    if idx_env.isdigit():
        return _CTA_POOL[int(idx_env) % len(_CTA_POOL)]
    seed = int(hashlib.md5(title.encode()).hexdigest(), 16)
    return _CTA_POOL[seed % len(_CTA_POOL)]


def _build_cta_segment(data, topic_spec):
    """Return a fully-formed segment dict for the CTA clip."""
    v = _pick_cta_variant(data)
    lang = (data.get("narration_lang") or NARRATION_LANG or "hi").strip().lower()
    text = v["hi"] if lang == "hi" else v["en"]
    beats = v["beat"]
    # Pad or trim beats to exactly _CTA_DURATION_S entries
    while len(beats) < _CTA_DURATION_S:
        beats = beats + [beats[-1]]
    beats = beats[:_CTA_DURATION_S]
    channel_name = os.getenv("CHANNEL_NAME", "DailyGeoMap")
    
    if lang == "hi":
        text = text.replace("Subscribe करें", f"{channel_name} को Subscribe करें")
        text = text.replace("हमें Subscribe", f"{channel_name} को Subscribe")
    else:
        text = text.replace("Subscribe", f"Subscribe to {channel_name}")
        
    vp = v["video_prompt"].rstrip(".") + f". Prominently feature the channel name '{channel_name}' and channel logo."
    
    return {
        "text": text,
        "speaker": "NARRATOR (V.O.)",
        "seconds": _CTA_DURATION_S,
        "_cta": True,           # marker so downstream code can identify this segment
        "video_prompt": vp,
        "per_second": beats,
        "background": {
            "type": "illustration",
            "description": "Channel branding close — animated map or globe motif, warm golden palette.",
        },
        "audio": {
            "ambience": "gentle uplifting swell",
            "music": "warm strings rise slightly to close",
            "sfx": [],
            "voice": "warm, inviting, direct address to viewer",
            "mix": "voice prominent, music swells gently at the end",
        },
    }


def maybe_append_cta(data, topic_spec):
    """Append a CTA segment if ≥ 6 s of headroom remain under PLATFORM_CAP_S.

    Skipped when:
    - APPEND_CTA=0 in env
    - A CTA segment already exists (reruns are idempotent)
    - Less than _CTA_DURATION_S seconds of headroom remain
    """
    if os.getenv("APPEND_CTA", "1").strip() == "0":
        return
    segments = data.get("segments") or []
    if any(s.get("_cta") for s in segments):
        return   # already present — idempotent on retry
    current_s = sum(float(s.get("seconds", 0)) for s in segments)
    headroom = PLATFORM_CAP_S - current_s
    if headroom < _CTA_DURATION_S:
        print(f"[CTA] skipped — only {headroom:.0f}s headroom (need {_CTA_DURATION_S}s)")
        return
    cta = _build_cta_segment(data, topic_spec)
    segments.append(cta)
    data["segments"] = segments
    data["requested_video_s"] = current_s + _CTA_DURATION_S
    print(f"[CTA] appended {_CTA_DURATION_S}s CTA — total now {data['requested_video_s']:.0f}s "
          f"({headroom:.0f}s was free)")


# The competitor's captions are one evocative line, an emoji, and exactly
# three specific hashtags. Ours were running to two sentences and five
# generic tags. The count is mechanical, so it is repaired rather than failed
# — but a tag can only be built from words the topic already carries, never
# invented, and #geography / #shorts are exactly the generic ones the
# teardown said not to use.
CAPTION_HASHTAGS = 3
GENERIC_TAGS = {"geography", "shorts", "reels", "viral", "explore", "trending",
                "india", "fyp", "foryou", "map", "maps", "facts", "gk"}


def _tagify(text):
    """'Uttar Pradesh' -> 'UttarPradesh', 'STATE ANIMALS' -> 'StateAnimals'.

    Returns '' for anything that would not read as a hashtag: too short, too
    long, or a whole phrase rather than a name.
    """
    words = re.findall(r"[A-Za-z0-9]+", str(text or ""))
    if not words or len(words) > 3:
        return ""
    joined = "".join(w[:1].upper() + (w[1:].lower() if w.isupper() else w[1:])
                     for w in words)
    return joined if 3 <= len(joined) <= 22 else ""


def normalize_caption(data, topic_spec):
    """One line, then exactly three specific hashtags.

    Extra tags are dropped from the end, generic ones first. Missing tags are
    built from the topic's own nouns — its subject, its series, the places it
    names — so the caption never carries a word the topic did not already
    have. Returns the caption it set.
    """
    raw = " ".join(str(data.get("caption", "")).split())
    tags, seen = [], set()
    for tag in re.findall(r"#([A-Za-z0-9_]+)", raw):
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    line = re.sub(r"#[A-Za-z0-9_]+", "", raw).strip()
    line = re.sub(r"\s{2,}", " ", line).strip(" -–—,")

    # Generic tags are dropped outright rather than kept as filler: the
    # teardown's point was that #geography does not place a post, and a slot
    # filled with it is a slot not naming the state.
    tags = [t for t in tags if t.lower() not in GENERIC_TAGS][:CAPTION_HASHTAGS]

    if len(tags) < CAPTION_HASHTAGS:
        scenes = [s for s in (data.get("map_scenes") or []) if isinstance(s, dict)]
        candidates = ([s.get("label") for s in scenes]
                      + [s.get("place") for s in scenes]
                      + [topic_spec.get("title_en"), topic_spec.get("group"),
                         topic_spec.get("subject"), topic_spec.get("series")])
        for candidate in candidates:
            if len(tags) >= CAPTION_HASHTAGS:
                break
            tag = _tagify(candidate)
            if tag and tag.lower() not in seen and tag.lower() not in GENERIC_TAGS:
                seen.add(tag.lower())
                tags.append(tag)

    caption = " ".join(filter(None, [line, " ".join("#" + t for t in tags)]))
    if caption != raw:
        print(f"[Prompt 2] caption normalised to one line + {len(tags)} hashtags")
    data["caption"] = caption
    return caption


def validate_set(data, topic_spec):
    """The two things a set video cannot ship without.

    The map walking the list IS the format, so a set script with almost no
    `map_scenes` is a single-subject video wearing a set's title — that is
    worth failing and redrawing. The closing question is mechanical, so it is
    repaired rather than failed: the topic already carries the exact wording.
    """
    scenes = [s for s in (data.get("map_scenes") or []) if isinstance(s, dict)
              and str(s.get("place", "")).strip()]
    if len(scenes) < 3:
        raise ScriptGenerationError(
            f"'{topic_spec['topic']}' is a set of {topic_spec.get('set_size')} but the "
            f"script returned {len(scenes)} usable map_scenes. The map walking the "
            f"list is the format; below 3 the renderer falls back to guessing the "
            f"pairings from prose, which is what got them crossed before."
        )
    data["map_scenes"] = scenes

    named = [str(s.get("place", "")).strip() for s in scenes]
    members = {str(m).strip().lower() for m in topic_spec.get("members") or []}
    stray = [p for p in named if members and p.lower() not in members]
    if stray:
        print(f"[Prompt 2] WARNING map_scenes name places outside the set: {stray}")

    ask = str(topic_spec.get("ask") or "").strip()
    caption = str(data.get("caption", "")).strip()
    if ask and "?" not in caption:
        data["caption"] = f"{caption} {ask}".strip()
        print(f"[Prompt 2] caption had no closing question — appended {ask!r}")

    print(f"[Prompt 2] set video: {len(scenes)} of "
          f"{topic_spec.get('set_size')} members named "
          f"({', '.join(named[:6])}{'...' if len(named) > 6 else ''})")



def set_brief(topic_spec):
    """The extra instruction a set topic needs, or "" for a single subject.

    A set topic arrives from Stage 0 with the list already decided —
    `members`, how many to `cover`, and the `ask` that closes it. Handing the
    model the list is the difference between a video that names eight states
    and one that names three and waffles; and it is what lets `map_scenes`
    be filled for every member instead of one.
    """
    if topic_spec.get("kind") != "set":
        return ""

    members = [str(m).strip() for m in topic_spec.get("members") or [] if str(m).strip()]
    if not members:
        return ""
    cover = int(topic_spec.get("cover") or min(len(members), 8))
    cover = max(3, min(cover, len(members)))
    ask = str(topic_spec.get("ask") or "Which one did I miss?").strip()

    return f"""
    THIS IS A SET VIDEO. The set is the video — not an introduction to one
    member with the rest mentioned at the end.

    The full set ({len(members)}): {', '.join(members)}

    - Name exactly {cover} of them, one per scene or tightly grouped, in a
      sensible geographic order (north to south, or coast inward) rather than
      the order above.
    - For each one you name, say the place AND the thing that belongs to it,
      paired correctly. A wrong pairing is the one mistake this format cannot
      survive — it is the whole comment section.
    - Leave the rest out deliberately. Do not apologise for it, do not say
      "and many more", and do not list the omitted names.
    - Close the caption with exactly this question: "{ask}"
    - `map_scenes` must have one entry for EVERY place you name — {cover}
      entries, each with the place, its label, and a figure. This is not
      optional on a set video: the map walking the list is the format.
    - `facts` must carry each pairing as its own checkable claim, in the form
      "The <thing> of <place> is <X>".
    """


def pick_reference_video(refs_dir, topic_spec):
    """Which reference reel to show the model, chosen rather than guessed.

    It used to be `random.choice`, which on a folder of one file was a
    no-decision and on a folder of many would swap the house style between
    runs. Order of preference:

      1. REFERENCE_VIDEO in .env — an absolute path, or a filename in the
         folder. Explicit beats clever.
      2. A filename that shares a distinctive word with this topic, its
         subject or its series, so a set video is shown a set video.
      3. The newest file, so adding a better reference makes it the default
         without renaming anything.

    Returns a path, or None when the folder is empty or missing.
    """
    if not refs_dir or not os.path.isdir(refs_dir):
        return None
    videos = [os.path.join(refs_dir, f) for f in sorted(os.listdir(refs_dir))
              if f.lower().endswith((".mp4", ".mov", ".m4v"))]
    if not videos:
        return None

    named = (os.getenv("REFERENCE_VIDEO") or "").strip()
    if named:
        candidate = named if os.path.isabs(named) else os.path.join(refs_dir, named)
        if os.path.exists(candidate):
            return candidate
        print(f"[Prompt 2] REFERENCE_VIDEO={named!r} not found in {refs_dir}")

    words = {w for w in re.findall(r"[a-z]{4,}", " ".join(str(topic_spec.get(k, ""))
             for k in ("topic", "subject", "series", "group")).lower())}
    if words:
        scored = []
        for path in videos:
            stem = set(re.findall(r"[a-z]{4,}", os.path.basename(path).lower()))
            overlap = len(stem & words)
            if overlap:
                scored.append((overlap, os.path.getmtime(path), path))
        if scored:
            scored.sort(reverse=True)
            return scored[0][2]

    return max(videos, key=os.path.getmtime)


def analyze_video_style(video_path, client):
    """Uses gemini to analyze the aesthetic style of the reference video."""
    print(f"[Prompt 2] Extracting frames from {video_path} for vision analysis...")
    frames = extract_frames_base64(video_path, num_frames=3)
    if not frames:
        return "Generic flat vector map animation"
        
    prompt = "Analyze these 3 frames from a viral geography video. Describe its exact visual aesthetic in 2 sentences. Focus heavily on the exact visual elements, e.g., 'Vintage parchment map with cute 2D paper cutout animations' or 'Hyper-realistic 3D Google Earth satellite zooming'. Be specific about map styles and animation techniques."
    
    contents = [prompt]
    import base64
    for f in frames:
        contents.append(
            types.Part.from_bytes(
                data=base64.b64decode(f),
                mime_type="image/jpeg"
            )
        )
        
    try:
        response, model = model_client.call_model(
            lambda m: client.models.generate_content(
                model=m,
                contents=contents,
            ),
            models=["gemini-3.5-flash-lite"],
            what="Prompt 2 Vision Style Analysis",
        )
        style = response.text.strip()
        print(f"[Prompt 2] Vision API extracted style: {style}")
        return style
    except Exception as e:
        print(f"[Prompt 2] Vision analysis failed: {e}. Falling back to default.")
        return "Flat 2D cartoon map animation."


def generate_script_prompt2(topic_spec):
    """Prompt 2: script, hook, segments, caption and a discrete facts array."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ConfigError("GEMINI_API_KEY or GOOGLE_API_KEY missing in environment.")

    subject = topic_spec.get("subject") or topic_spec["topic"]

    client = genai.Client(api_key=api_key)
    
    # 1. Select reference video and extract its dynamic visual style
    refs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand", "references")
    ref_video = pick_reference_video(refs_dir, topic_spec)
    dynamic_style = "Flat 2D geography illustration"
    if ref_video:
        dynamic_style = analyze_video_style(ref_video, client)

    video_file_obj = None
    if "downloaded_video_path" in topic_spec:
        import time
        video_path = topic_spec["downloaded_video_path"]
        print(f"[Prompt 2] Uploading {video_path} to Gemini...")
        video_file_obj = client.files.upload(file=video_path)
        print(f"[Prompt 2] Video uploaded as {video_file_obj.name}. Waiting for processing...")
        while video_file_obj.state.name == "PROCESSING":
            time.sleep(3)
            video_file_obj = client.files.get(name=video_file_obj.name)
            print(".", end="", flush=True)
        print()
        if video_file_obj.state.name == "FAILED":
            raise ScriptGenerationError(f"Gemini failed to process the video: {video_path}")
        print("[Prompt 2] Video processing complete!")

    source_nm, source_notes = None, None
    if NARRATION_MODE == "rewrite" and video_file_obj is not None:
        source_nm = narration_map.extract(client, video_file_obj,
                                          call_model=model_client.call_model, types=types)
        if not source_nm:
            raise ScriptGenerationError("Rewrite mode: could not transcribe the source video.")
        source_notes = source_facts.extract_facts(client, source_nm,
                                                  call_model=model_client.call_model, types=types)
        if not source_notes:
            raise ScriptGenerationError("Rewrite mode: no usable facts in the source video.")
        print(f"[Rewrite] {len(source_nm['lines'])} source line(s) -> {len(source_notes)} fact note(s); "
              f"the scriptwriter will not see their video")
        for n in source_notes:
            print(f"[Rewrite]   {n}")
        video_file_obj = None   # no sound map, no frame copying, no transcript in the prompt

    # Their sound design, heard on its own before the script is written: a
    # single pass over script + visuals + sound hears the words and loses the
    # firecracker under them.
    sm = sound_map.extract(client, video_file_obj, topic_spec.get("downloaded_video_path"),
                           call_model=model_client.call_model, types=types)
    if sm:
        print(f"[SoundMap] {len(sm['events'])} sound event(s), music: "
              f"{(sm.get('music') or {}).get('style') or 'none'}")
        for e in sm["events"]:
            print(f"[SoundMap]   {e['id']} {e['at']:>5}s {e['loudness']:<6} {e['sound']}")

    nm = None
    if NARRATION_MODE == "faithful":
        nm = narration_map.extract(client, video_file_obj,
                                   call_model=model_client.call_model, types=types)
        if nm:
            print(f"[NarrationMap] {len(nm['lines'])} line(s), {nm['words']} words, {nm['language']}")
            for ln in nm["lines"]:
                print(f"[NarrationMap]   {ln['id']:>4} {ln['start']:>5}-{ln['end']:<5}s {ln['text'][:90]}")
    faithful = bool(nm)

    competitor_s = float((sm or {}).get("duration_s") or 0) or _probe_seconds(
        topic_spec.get("downloaded_video_path"))
    window = runtime_window(competitor_s)
    if window:
        lo, hi = window
        w_lo, w_hi = int(lo * WORDS_PER_S), int(hi * WORDS_PER_S)
        capped = competitor_s > PLATFORM_CAP_S
        print(f"[Prompt 2] length follows the competitor ({competitor_s:.1f}s): "
              f"aim {lo:.0f}-{hi:.0f}s, {w_lo}-{w_hi} words"
              + (f" [CAPPED at {PLATFORM_CAP_S}s platform limit]" if capped else ""))
        cap_note = (f" The video is a 9:16 Short/Reel — the hard platform limit is "
                    f"{PLATFORM_CAP_S} seconds (3 minutes). The total of all scene 'seconds' "
                    f"MUST NOT exceed {PLATFORM_CAP_S}."
                    if capped else
                    f" Hard platform limit: total scene 'seconds' must not exceed {PLATFORM_CAP_S}s.")
        length_brief = (f"LENGTH: the competitor video runs {competitor_s:.0f} seconds. Ours matches it — "
                        f"about {lo:.0f}-{hi:.0f} seconds spoken. Cover EVERYTHING they cover: every "
                        f"member, place and beat, in their order. Never drop an item to save time."
                        + cap_note)
        if source_notes:
            length_brief = (f"LENGTH: about {lo:.0f}-{hi:.0f} seconds spoken. Cover the research "
                            f"notes that make the best story, in the order YOU choose." + cap_note)
        words_rule = (f"WORD COUNT: the TOTAL words across all 'text' fields combined should be about "
                      f"{w_lo}-{w_hi} (that is {lo:.0f}-{hi:.0f} seconds spoken). "
                      f"Under {int(lo * SHORT_TOLERANCE * WORDS_PER_S)} "
                      f"words is rejected because it means content was dropped. "
                      f"Total scene 'seconds' must not exceed {PLATFORM_CAP_S}. "
                      f"CRITICAL TTS LIMIT: Max 3.4 words per second. For any segment, (word_count / seconds) MUST be <= 3.4.")
        scenes_rule = (f"Use as many scenes as the content needs — roughly {int(math.ceil(lo / 8))} to "
                       f"{int(math.ceil(hi / 6))} at this length, more if needed. "
                       f"Total 'seconds' across all scenes must not exceed {PLATFORM_CAP_S}.")
        sys_length = (f"Match the competitor's length: about {w_lo}-{w_hi} words of narration. "
                      f"Total scene 'seconds' must not exceed {PLATFORM_CAP_S}. "
                      f"CRITICAL TTS LIMIT: Max 3.4 words per second per segment.")
    if faithful:
        max_words_cap = int(PLATFORM_CAP_S * WORDS_PER_S)
        budget = int((window[1] if window else PLATFORM_CAP_S) * FLOW_HINDI_WPS)
        words_rule = (f"WORD BUDGET: at most about {budget} Hindi words in total across all 'text' "
                      f"fields — that is how many fit in the competitor's runtime at Flow's Hindi "
                      f"speaking rate. Every line is still translated; stay inside the budget by "
                      f"saying things in fewer, simpler spoken words, never by dropping a line. "
                      f"Total scene 'seconds' must not exceed {PLATFORM_CAP_S} "
                      f"(≈{max_words_cap} words). Nothing cut to save words except to stay within the "
                      f"3-minute platform limit. "
                      f"CRITICAL TTS LIMIT: Max 3.4 words per second. For any segment, (word_count / seconds) MUST be <= 3.4.")
        sys_length = ("Translate the competitor's narration line by line into Hindi: every line, "
                      f"in order, nothing added, nothing dropped. Total 'seconds' ≤{PLATFORM_CAP_S}. "
                      f"CRITICAL TTS LIMIT: Max 3.4 words per second per segment.")
    if not window:
        max_words_cap = int(PLATFORM_CAP_S * WORDS_PER_S)
        length_brief = (f"LENGTH: no fixed length. Tell the whole topic; the content decides how long it runs. "
                        f"Hard platform limit: total scene 'seconds' must not exceed {PLATFORM_CAP_S}s (3 minutes).")
        scenes_rule = f"Use as many scenes as the content needs. Total 'seconds' must not exceed {PLATFORM_CAP_S}."
        if not faithful:
            words_rule = (f"WORD COUNT: as many words as the topic needs, up to ~{max_words_cap} "
                          f"(the {PLATFORM_CAP_S}s platform ceiling for 9:16 Shorts/Reels). "
                          f"CRITICAL TTS LIMIT: Max 3.4 words per second. For any segment, (word_count / seconds) MUST be <= 3.4.")
            sys_length = (f"The content decides the length; stay within the {PLATFORM_CAP_S}s platform limit. "
                          f"CRITICAL TTS LIMIT: Max 3.4 words per second per segment.")
    refs_field = ('"narration_refs": ["<ids of the competitor lines this scene translates, e.g. N3, N4>"],'
                  if faithful else "")
    competitor_rule = (
        "WATCH THE ATTACHED COMPETITOR VIDEO. You must replicate its visuals EXACTLY frame-by-frame. "
        "The narration is THEIR narration, translated (see THE COMPETITOR'S NARRATION above). "
        "For each segment, your 'video_prompt' and 'per_second' visual breakdowns must match what is "
        "on screen in their video while those lines are spoken."
        if faithful else
        "WATCH THE ATTACHED COMPETITOR VIDEO. You must replicate its visuals EXACTLY frame-by-frame. "
        "For the script, use their spoken audio as a baseline but adapt it into your own words (some "
        "creators speak artificially fast; do not transcribe them word-for-word), keeping every item "
        "and beat they cover. For each segment, your 'video_prompt' and 'per_second' visual "
        "breakdowns must match the competitor's visual sequence.") if video_file_obj else ""

    prompt = f"""
    You write scripts for vertical geography videos aimed at an Indian audience,
    including competitive-exam aspirants.

    YOUR TOPIC IS: "{subject}"
    {set_brief(topic_spec)}
    {(HINDI_FAITHFUL_BRIEF if faithful else HINDI_BRIEF) if NARRATION_LANG == "hi" else ""}
    {"" if faithful else STORY_BRIEF}
    {narration_map.brief(nm, NARRATION_LANG)}
    {source_facts.brief(source_notes)}
    {sound_map.brief(sm)}

    {length_brief}

    {competitor_rule}

    VISUAL STYLE TO EMULATE:
    {dynamic_style}

    Return JSON only with this exact shape:

    {{
      "title": "<max 4 words, all caps, fits on a cover image>",
      "title_regional": "<the same title in the locally relevant script (Devanagari, Telugu, Tamil, Bengali, Assamese...) — for a non-Indian subject, return the English title>",
      "segments": [
        {{
          "text": "<spoken line for this scene>",
          "speaker": "NARRATOR (V.O.)",
          {refs_field}
          "seconds": 8,
          "labels": ["<0-3 short on-screen labels IN ENGLISH LETTERS that we overlay ourselves: the place names shown and EVERY number this scene's line says, as digits with unit, e.g. 'Kosi River', '21 lakh L/s', '450 km'>"],
          "video_prompt": "<the one continuous shot, in a sentence or two — NO written text in the picture>",
          "per_second": [
            "<second 0-1: what is in frame as the take opens, where the camera is>",
            "<second 1-2: what changes>",
            "<second 2-3: what changes>",
            "<second 3-4: what changes>",
            "<second 4-5: what changes>",
            "<second 5-6: what changes>",
            "<second 6-7: what changes>",
            "<second 7-8: where the move settles>"
          ],
          "background": {{
            "type": "<map | illustration | photo_plate | texture | solid>",
            "description": "<what the plate behind this scene is, in a sentence>",
            "map": {{
              "extent": "<what area the map covers, e.g. 'the whole of India, the state small inside it'>",
              "highlight_list": [{{"place": "<the region filled in colour>", "label": "<what to name it>"}}],
              "figure": "<what stands on the highlighted region, if anything>",
              "ground": "<what the rest of the map looks like>",
              "features": ["<rivers, borders, coastline the map must show>"],
              "camera": "<what the map itself does: hold, or zoom from wide to the region>"
            }}
          }},
          "audio": {{
            "ambience": "<the natural sound bed under this scene>",
            "music": "<what the score is doing here — instrument, movement, dynamics>",
            "sfx": [{{"at": 0.0, "cue": "<spot effect and the second it lands on>"}}],
            "voice": "<how this line is read: pace, weight, where it lifts>",
            "mix": "<levels, and what ducks under what>"
          }}
        }}
      ],
      "map_scenes": [
        {{"place": "<the state, region, country or river this beat is about, spelled as it appears on a map>",
          "label": "<what to name on screen for it: the dance, the animal, the peak, the dish>",
          "figure": "<what the illustration should show: a person, animal or object, in a few words>",
          "seconds": 8}}
      ],
      "midroll_cta": "<one fresh Hindi subscribe line for THIS video, max 10 words>",
      "midroll_after": 6,
      "caption": "<ONE evocative line, then exactly 3 specific hashtags>",
      "upload_title": "<an SEO-friendly video title optimized for YouTube/Instagram, max 60 characters>",
      "upload_hashtags": ["<seo-friendly tag 1>", "<seo-friendly tag 2>", "<seo-friendly tag 3>", "<seo-friendly tag 4>", "<seo-friendly tag 5>"],
      "facts": ["<every discrete factual claim made, one per string>"],
      "original_content_link": "{'' if source_notes else topic_spec.get('url', '')}"
    }}

    Rules:
    - NO TEXT IN THE PICTURE. The video model misspells every word it draws
      ("Ghapra", "MANFAJPUR"). "video_prompt", "per_second" and "background"
      must never ask for written words, labels, place names, numbers, titles,
      signs or map text — show places with pins, glowing river lines, colour
      fills, icons and camera moves only. Names and numbers go in "labels";
      we put those on screen ourselves, spelled correctly.
    - SPOKEN TEXT IS DEVANAGARI ONLY: every name, dish and English word in
      "text" is written in Hindi letters (थलासेरी बिरयानी, फिश मोली, स्टू),
      never in English letters — the voice reads English letters with an
      English accent. English letters belong only in "labels".
    - LABELS are a place, a dish, a landmark, or a number the narrator
      actually says. No filler counts ("4 Dishes", "3 Places") unless the
      narrator says that number.
    - NUMBERS: every number the narration says (distances, years, discharge,
      counts, percentages) is kept — in the spoken line AND as digits in that
      scene's "labels". A script that loses one of their numbers is rejected.
    - {words_rule} Short, punchy sentences — about 12 words for a 6-second scene, 16 for 8 seconds, 20 for 10. The clip is cut to the line's length, so a line much shorter than its scene leaves dead air.
    - Write about YOUR TOPIC above. The script is your own: original wording, your own structure and angle.
    - MID-ROLL SUBSCRIBE LINE: write ONE fresh line in "midroll_cta" — everyday spoken Hindi, at most 10 words, asking the viewer to subscribe to मैप एंड लेजेंड (or "चैनल"), written for THIS video: it picks up from what the scene before it just revealed and leans into what comes next, so it sounds like part of the story, not an ad. Never generic, never a stock phrase. Put the scene number it follows (a scene around the middle that ends on a full stop, not a question or a joke setup) in "midroll_after". Do not put any like/comment/subscribe words in the scenes' "text" yourself.{_recent_cta_note()}
    - Every scene's "seconds" must be exactly 4, 6, 8 or 10. No other value —
      not 5, not 12, not 15. These are the lengths a text-to-video model
      actually generates, so a scene is one generation and never a join.
    - {scenes_rule} Vary the lengths: a run of equal
      scenes reads as a slideshow. Open on a 4s or 6s scene.
    - "per_second" must have exactly as many entries as the scene has seconds —
      8 entries for an 8-second scene. Entry i describes second i to i+1.
    - "speaker" names the voice that says this scene's line. This is a faceless
      channel: unless there is a reason for a second voice, every scene is
      "NARRATOR (V.O.)". If a scene genuinely needs another voice — a chant, a
      crowd, a called-out word — name it as a role in caps ("PRIEST (O.S.)",
      "CROWD"), never as a real or named person, and put only that voice's
      words in this scene's "text".
    - Do NOT put the spoken words inside "per_second" or "video_prompt". The
      sheet places each word on the second it is spoken, attributed to
      "speaker", from "text" alone — writing them twice desynchronises them.
    - Every factual claim must also appear in the `facts` array, phrased so it can be checked
      on its own without the surrounding script. An unverifiable claim in the script that is
      missing from `facts` defeats the downstream gate.
    - Each fact must be specific and falsifiable — a name, a number, a designation. Do not
      write self-referential filler such as "X is an established geographical subject".
    - Do not state a fact you are not confident is current. Prefer stable facts (geography,
      official designations) over changeable ones (rankings, populations, records).
    - No "largest/longest/highest" claim unless it is an official designation.
    - {"Plain spoken Hindi narration (Devanagari). No jargon, no filler." if NARRATION_LANG == "hi" else "Plain spoken Indian English. No jargon, no filler."}
    - `map_scenes` is what the renderer draws: each entry highlights one place
      on the map of the region, names it, and stands an illustrated `figure` on
      it. Fill it for any topic where places carry the content — one entry per
      member of the set, in the order the narration reaches them. `place` must
      be a real place as a map spells it ("Odisha", not "the East"), and the
      `label` must be the thing that belongs to THAT place, not to its
      neighbour: pair them yourself rather than leaving it to be guessed.
      Leave `map_scenes` as an empty list when the topic has no such places.
    - If the topic reads as a SET even without a SET VIDEO brief above — the
      classical dances, the state animals, the temples of one state, the peaks
      — treat it as one: name the members, pair each with its state so the map
      can follow, and close the caption by asking which was missed. On this
      niche, a list about someone's home state is something they correct in
      the comments, and that is what carries the post.
    - Caption: one evocative line and exactly three hashtags. Not two sentences,
      not five tags, and not generic ones — #Geography, #Shorts, #Maps, #Facts
      and #India are all too broad to place the post. Tag the specific thing:
      the state, the river, the dance, the dish.

    Video prompts — these go to a text-to-video model, so write a SHOT, not a
    still:
    - CRITICAL: You must write `video_prompt`s that meticulously emulate the VISUAL STYLE TO EMULATE described above.
      Your `video_prompt`s MUST explicitly dictate this exact aesthetic in every scene.
    - One per scene, as a single continuous take. Name the subject, what moves
      in the frame, and how the camera moves (slow push in, drift left, tilt
      up, static). Say where the light comes from.
    - Keep each to one shot. No cuts, no montage, no "then" — a scene that
      needs two ideas should be two scenes.
    - Nothing that has to be read: no text, captions, titles, signage, logos,
      or maps with place names on them. The renderer burns in all the text.
    - No real, living or identifiable people, and no recognisable faces in
      close-up.
    - Do not name the visual style; a house style is appended automatically.

    The per-second beats:
    - They are moments inside ONE take, not a shot list. Never write a cut, a
      new location, or a new subject halfway through — the camera and the
      subject at second 7 are the ones from second 0, further along.
    - Each entry says what is different about that second: the camera a little
      closer, a bird crossing frame, the light moving, a hand finishing what it
      started. "Same as before" is not an entry.
    - Write them so a model reading only that one line still knows what is in
      frame. Name the subject in each, not "it".
    - Same bans as the shot: no text, signage or labels; no recognisable faces.

    The background:
    - Every scene says what it sits on. "type" is one of: map, illustration,
      photo_plate, texture, solid. Say it even when it is obvious.
    - Give "map" ONLY when the scene actually shows a map, and then fill it
      properly: what area is in frame, which region is filled in colour, what
      that region should be named, and what the rest of the map looks like.
      A map with nothing highlighted is a wallpaper, not a shot.
    - "highlight_list" pairs each highlighted place with the thing that belongs
      to THAT place — the dance, the dish, the animal. Pair them yourself.
      Where a "map_scenes" entry already covers this beat, keep them consistent.
    - "ground" is what everything outside the highlight looks like. The house
      style is every other region desaturated, so the highlight is the only
      saturated thing on screen.
    - For any other type, "description" alone carries it: what the plate shows,
      its depth, where the light falls. Omit the "map" object entirely.
    - The generated background must carry NO text, names, labels, legends or
      numbers. The renderer burns every word in afterwards. A map with place
      names baked into the image is unusable.
    - Compose the subject above the caption band: the bottom ~520px of the
      1080x1920 frame is covered by burned-in captions.

    The audio:
    - "ambience" is the bed a microphone in that place would pick up, not music.
    - "music" describes instruments and movement, and must stay out of the way
      of the read. Name what it does at this point — enters, holds, drops out.
    - "sfx" lists spot effects with the second each lands on, relative to the
      start of THIS scene. If a reference sound list is given above, each sound
      assigned to this scene goes here with its "ref". Beyond those, add one only where a cut or a movement
      wants punctuation; never invent filler.
    - "voice" names only the word that carries the emphasis. The narrator is
      the SAME man at the SAME calm pace and pitch in every scene: never ask
      for a different tone, energy or mood per scene — each clip is voiced
      separately, and a per-scene tone change is heard as a different person.
    - "mix" is levels. Narration always in front of music.
    - Describe sound that belongs to the place or the object. Never name a
      track, an artist, a song or a library cue.
    """

    # The validator's reasons are precise ("scenes [3, 7] start cold", "the hook
    # promises four parts but never counts them"). Hand them back and retry the
    # SAME topic, instead of spending the next competitor video to learn it.
    correction = ""
    for script_try in range(1, STORY_RETRIES + 1):
        full = prompt + (f"""

    YOUR PREVIOUS ATTEMPT WAS REJECTED BY THE VALIDATOR:
    > {correction}
    Fix exactly that and return the whole JSON again.
    """ if correction else "")
        contents = [video_file_obj, full] if video_file_obj else [full]

        try:
            response, model = model_client.call_model(
                lambda m: client.models.generate_content(
                    model=m,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=("You are a professional scriptwriter for geography videos. " + sys_length
                                            + ("Write all narration (segment 'text'), 'caption', 'upload_title' and 'title_regional' in Hindi, Devanagari script; keep every other field in English. Every JSON key must be in double quotes. " if NARRATION_LANG == "hi" else "")
                                            + "Always return ONLY valid JSON inside a ```json block."),
                        max_output_tokens=65536,   # long remakes: ~800 tokens a scene; never truncate
                        response_mime_type="application/json",
                        safety_settings=[
                            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE")
                        ]
                    )
                ),
                models=["gemini-3.5-flash-lite"],
                what="Prompt 2 script",
            )
        except QuotaExhaustedError:
            raise  # account-level: the run stops, it does not try another topic
        except Exception as e:
            raise ScriptGenerationError(
                f"Gemini text call failed: {type(e).__name__}: {e}\n"
                f"Check your API key."
            ) from e

        content = response.text
        if not content:
            raise ScriptGenerationError(
                f"Model returned an empty response"
            )

        try:
            data = validate_script(_extract_json(content), topic_spec, window, faithful)
            if nm:
                dropped = narration_map.missing(data["segments"], nm)
                problem = narration_map.order_problem(data["segments"], nm)
                if (dropped or problem) and script_try < STORY_RETRIES:
                    raise ScriptGenerationError(narration_map.reason(dropped, problem))
                if len(dropped) > MAX_DROPPED_LINES * len(nm["lines"]):
                    raise ScriptGenerationError(narration_map.reason(dropped, problem))
                if dropped or problem:
                    print(f"[NarrationMap] accepted with {len(dropped)} line(s) not referenced"
                          + (f"; {problem}" if problem else ""))
                data["narration_map"] = nm
            latin = latin_in_text(data["segments"])
            if latin and script_try < STORY_RETRIES:
                raise ScriptGenerationError(
                    "English letters in the spoken text: "
                    + "; ".join(f"scene {i + 1}: {', '.join(ws)}" for i, ws in latin)
                    + ". Write every name and word in Devanagari (e.g. थलासेरी बिरयानी).")
            if latin:
                print(f"[Prompt 2] accepted with English letters in the narration: {latin}")
            if nm:
                drop_filler_labels(data["segments"], nm)
            if nm:
                lost = missing_numbers(data["segments"], nm)
                if lost and script_try < STORY_RETRIES:
                    raise ScriptGenerationError(
                        "Numbers from the competitor are missing: "
                        + "; ".join(f"{lid} says {', '.join(ns)}" for lid, ns in lost)
                        + ". Keep each one in the spoken line and as digits in that scene's labels.")
                if lost:
                    print(f"[Prompt 2] accepted with numbers missing: {lost}")
            if faithful and window:
                budget = int(window[1] * FLOW_HINDI_WPS)
                total = sum(len(str(sg.get("text", "")).split()) for sg in data["segments"])
                if total > budget * 1.12 and script_try < STORY_RETRIES:
                    raise ScriptGenerationError(
                        f"Too long: {total} Hindi words, the budget is about {budget} "
                        f"(the competitor runs {window[1]:.0f}s at most). Keep every line but "
                        f"say each one in fewer, everyday spoken words — cut filler, not facts.")
                if total > budget * 1.12:
                    print(f"[Prompt 2] accepted over budget: {total} words vs {budget}")
            if source_nm:
                why = source_facts.check_originality(client, source_nm, data["segments"],
                                                     call_model=model_client.call_model, types=types)
                if why == "__unchecked__":
                    raise ScriptGenerationError(
                        "Rewrite mode: the originality check could not run, so the script is not published.")
                if why and script_try < STORY_RETRIES:
                    raise ScriptGenerationError(why)
                if why:
                    raise ScriptGenerationError(f"Rewrite mode: still too close after {STORY_RETRIES} tries. {why}")
                print("[Rewrite] originality check passed: facts shared, wording and order our own")
                data["source_notes"] = source_notes
                # Would a viewer follow it? Hook, one-way geography, true links,
                # exact facts, an honest CTA, a payoff, Hindi spellings.
                review, unspoken = source_facts.viewer_review(
                    client, data, call_model=model_client.call_model, types=types)
                if review and script_try < STORY_RETRIES:
                    raise ScriptGenerationError(review)
                if review:
                    print(f"[Review] accepted with open notes after {STORY_RETRIES} tries")
                    data["review_notes"] = review
                gone = source_facts.drop_labels(data["segments"], unspoken)
                if gone:
                    print(f"[Review] removed {gone} label(s) the narration never says: {unspoken}")
            miss = sound_map.missing(data["segments"], sm)
            if miss and script_try < STORY_RETRIES:
                raise ScriptGenerationError(sound_map.missing_reason(miss))
            placed = sound_map.fill_missing(data["segments"], sm)
            if placed:
                print(f"[SoundMap] model still dropped {placed} — placed by time")
            if sm:
                data["sound_map"] = sm
            if os.getenv("MIDROLL_CTA", "1") != "0" and NARRATION_LANG == "hi":
                ok, why = _valid_cta(data.get("midroll_cta"), _recent_ctas(50))
                if not ok:
                    # A good script is not thrown away for one line: ask for the line alone.
                    print(f"[CTA] script's midroll_cta unusable ({why}) — asking for the line alone")
                    data["midroll_cta"] = write_midroll_cta(client, types, data)
            insert_midroll_cta(data)
            break
        except ScriptGenerationError as err:
            if script_try == STORY_RETRIES:
                raise
            correction = str(err)
            print(f"[Prompt 2] attempt {script_try} rejected — retrying with the reason: {correction[:160]}")
    print(
        f"[Prompt 2] Script for '{subject}': {len(data.get('segments', []))} scenes, "
        f"est. audio {data.get('estimated_audio_s')}s, "
        f"{len(data['facts'])} checkable claims, "
        f"{sum(len(s['per_second']) for s in data['segments'])} per-second prompts"
    )
    return data


if __name__ == "__main__":
    spec = {
        "topic": "Course and Origin of River Godavari",
        "subject": "River Godavari",
        "series": "Sacred Rivers",
        "title_en": "GODAVARI",
    }
    print(json.dumps(generate_script_prompt2(spec), indent=2, ensure_ascii=False))
