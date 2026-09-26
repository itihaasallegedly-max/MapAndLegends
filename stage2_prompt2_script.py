"""Prompt 2 — script generation.

There is no template fallback in this module any more. If the model cannot
be reached, or returns something that does not satisfy the contract below,
this raises. A run that cannot write a real script must not produce a video.
"""
import json
import math
import os
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

from core import model_client, sound_map
from pipeline_errors import ConfigError, QuotaExhaustedError, ScriptGenerationError
from utils.frame_extractor import extract_frames_base64

load_dotenv()

# The target window, and the validator's tolerance around it. A set topic —
# eight dances, eight states — cannot be told in 50 seconds; the competitor's
# version of exactly that runs 100. Override per run:
#   RUNTIME_TARGET_MIN_S=80 RUNTIME_TARGET_MAX_S=100 ./venv/bin/python pipeline_daily.py ...
TARGET_MIN_S = float(os.getenv("RUNTIME_TARGET_MIN_S", "45"))
TARGET_MAX_S = float(os.getenv("RUNTIME_TARGET_MAX_S", "60"))
MIN_RUNTIME_S = TARGET_MIN_S - 5
MAX_RUNTIME_S = TARGET_MAX_S + 5
REQUIRED_KEYS = ("title", "hook", "segments", "caption", "facts")

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
    translate line by line. Every segment's "text" and the "hook" must be in
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


STORY_RETRIES = int(os.getenv("PROMPT2_STORY_RETRIES", "3"))
STORY_BRIEF = """
    THE NARRATION IS ONE STORY, NOT A LIST OF FACTS.
    Each scene's line is voiced separately, clip by clip, so the only thing that
    carries the viewer from one clip to the next is the words. Write it so that
    a viewer who hears only the narration follows one thread from start to end:
    - Segment 1's "text" IS the hook, word for word the same as "hook": a
      surprising claim or a question the video then answers. Never open on a
      flat definition ("X is a country in ...").
    - Every later line picks up from the line before it. Start it with a
      linking word or a reference back ("लेकिन", "फिर", "इसीलिए", "इसी वजह से",
      "तो", "इसके बाद", "जवाब में", "तब", "यानी", "आखिर में", "यह", "इसी",
      "दोनों", ...). No line may start a new subject cold.
    - Cause before effect. If a line says something happened (a coup, a war, a
      partition, a treaty), that line or the one before it says who did it and
      why. "After a coup, Turkey occupied the north" without whose coup and
      what it wanted is a fact, not a story.
    - Keep every promise. If the hook names a number or asks a question ("four
      parts", "why?"), the script delivers exactly that: name each item as the
      story reaches it ("पहला ...", "दूसरा ...", "तीसरा ..."), and do not
      leave one out.
    - The last segment is the payoff: it answers the hook in one line and
      closes the loop. Never end on a mid-story fact.
    - Chronology runs one way. If you jump back in time after the hook, say so
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


def check_story(segments, hook):
    """The narration must read as one thread. Raises with what to fix."""
    first = _norm(segments[0].get("text", ""))
    h = _norm(hook)
    if h and h not in first and first not in h:
        raise ScriptGenerationError(
            "Segment 1's text must BE the hook, word for word. The hook was "
            f"\"{hook}\" but segment 1 says \"{segments[0].get('text', '')}\". "
            "Put the hook as segment 1's spoken line.")
    if NARRATION_LANG != "hi" or len(segments) < 3:
        return
    cold = [i for i, sg in enumerate(segments[1:], 2)
            if not LINKERS.intersection(_norm(sg.get("text", "")).split()[:4])]
    allowed = (len(segments) - 1) // 4          # one in four may link by content alone
    if len(cold) > allowed:
        raise ScriptGenerationError(
            f"Scenes {cold} start a new subject cold. Each line after the hook must pick up "
            f"from the one before: open it with a linking word (लेकिन, फिर, इसीलिए, तो, "
            f"इसके बाद, जवाब में, यानी, आखिर में ...) or refer back to what was just said.")
    # A hook that promises a count ("चार हिस्से") must be counted off.
    words = h.split()
    for k, w in enumerate(words[:-1]):
        n = COUNT_WORDS.get(w)
        if n and any(words[k + 1].startswith(c) for c in COUNT_NOUNS):
            body = set(_norm(" ".join(str(sg.get("text", "")) for sg in segments[1:])).split())
            missing = [ORDINALS[j][0] for j in range(n - 1) if not body.intersection(ORDINALS[j])]
            if missing:
                raise ScriptGenerationError(
                    f"The hook promises {w} {words[k + 1]}, but the script never counts them off "
                    f"(missing: {', '.join(missing)}). Name each one as the story reaches it "
                    f"— पहला ..., दूसरा ... — and let the last line name the final one.")
            break

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


def validate_script(data, topic_spec):
    """Enforce the contract. Returns the script; raises on any violation."""
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ScriptGenerationError(f"Script missing required keys: {missing}")

    segments = data["segments"]
    if not isinstance(segments, list) or len(segments) < 3:
        raise ScriptGenerationError(
            f"Expected at least 3 segments, got {len(segments) if isinstance(segments, list) else type(segments).__name__}"
        )
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

    # The scene grammar: 4/6/8/10 seconds a scene, one prompt per second, a
    # full audio description per scene. A model that answered with 15-second
    # scenes, or left `per_second` out, is corrected here rather than failing
    # the run — the correction only ever rewrites timing and camera language.
    subject_name = topic_spec.get("subject") or topic_spec.get("topic", "")
    # (Previously scene_grammar.apply was called here. Now we just trust the model's segments)
    data["segments"] = segments

    check_story(segments, data.get("hook", ""))

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
    
    if estimated_audio_s < MIN_RUNTIME_S:
        raise ScriptGenerationError(
            f"Script is too short: estimated audio is {estimated_audio_s:.1f}s "
            f"({total_words} words), minimum is {MIN_RUNTIME_S}s."
        )
    if estimated_audio_s > MAX_RUNTIME_S:
        raise ScriptGenerationError(
            f"Script is too long: estimated audio is {estimated_audio_s:.1f}s "
            f"({total_words} words), maximum is {MAX_RUNTIME_S}s."
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

    return data



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

    prompt = f"""
    You write scripts for {int(TARGET_MIN_S)}-{int(TARGET_MAX_S)} second vertical geography shorts aimed at an Indian audience,
    including competitive-exam aspirants.

    YOUR TOPIC IS: "{subject}"
    {set_brief(topic_spec)}
    {HINDI_BRIEF if NARRATION_LANG == "hi" else ""}
    {STORY_BRIEF}
    {sound_map.brief(sm)}

    {"WATCH THE ATTACHED COMPETITOR VIDEO. You must replicate its visuals EXACTLY frame-by-frame. For the script, use their spoken audio as a baseline but adapt it to 105-135 words total (Some creators speak artificially fast; do not transcribe them word-for-word). If their video is short, ADD context, a cause or a consequence so the narration still reaches 105 words — a script under 95 words is rejected. For each segment, your 'video_prompt' and 'per_second' visual breakdowns must match the competitor's visual sequence." if video_file_obj else ""}

    VISUAL STYLE TO EMULATE:
    {dynamic_style}

    Return JSON only with this exact shape:

    {{
      "title": "<max 4 words, all caps, fits on a cover image>",
      "title_regional": "<the same title in the locally relevant script (Devanagari, Telugu, Tamil, Bengali, Assamese...) — for a non-Indian subject, return the English title>",
      "hook": "<the opening line — a question or a surprising claim; segment 1's text is exactly this>",
      "segments": [
        {{
          "text": "<spoken line for this scene>",
          "speaker": "NARRATOR (V.O.)",
          "seconds": 8,
          "video_prompt": "<the one continuous shot, in a sentence or two>",
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
      "caption": "<ONE evocative line, then exactly 3 specific hashtags>",
      "upload_title": "<an SEO-friendly video title optimized for YouTube/Instagram, max 60 characters>",
      "upload_hashtags": ["<seo-friendly tag 1>", "<seo-friendly tag 2>", "<seo-friendly tag 3>", "<seo-friendly tag 4>", "<seo-friendly tag 5>"],
      "facts": ["<every discrete factual claim made, one per string>"],
      "original_content_link": "<the exact link to the competitor video provided (e.g. {topic_spec.get('url', 'https://www.youtube.com/@DailyGeoMap')})>"
    }}

    Rules:
    - CRITICAL WORD COUNT: the TOTAL words across all 'text' fields combined must be BETWEEN 105 AND 135 (that is {int(TARGET_MIN_S)}-{int(TARGET_MAX_S)} seconds spoken). Under 95 words is rejected as too short; over 140 is rejected as too long. Count before you answer. Short, punchy sentences — about 12 words for a 6-second scene, 16 for 8 seconds, 20 for 10. The clip is cut to the line's length, so a line much shorter than its scene leaves dead air.
    - Write about the topic presented in the video.
    - Keep your response concise (maximum 8 segments) so that the JSON does not get truncated.
    - Every scene's "seconds" must be exactly 4, 6, 8 or 10. No other value —
      not 5, not 12, not 15. These are the lengths a text-to-video model
      actually generates, so a scene is one generation and never a join.
    - Plan {int(math.ceil(TARGET_MIN_S / 8))} to {int(TARGET_MAX_S // 6)} scenes, and make them sum to between
      {int(TARGET_MIN_S)} and {int(TARGET_MAX_S)} seconds. Vary the lengths: a run of equal
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
      start of THIS scene. Every competitor sound assigned to this scene goes
      here with its "ref". Beyond those, add one only where a cut or a movement
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
                        system_instruction=("You are a professional scriptwriter for geography shorts. Narration totals 105-135 words across all segments — never fewer than 95. "
                                            + ("Write all narration (segment 'text' and 'hook'), 'caption', 'upload_title' and 'title_regional' in Hindi, Devanagari script; keep every other field in English. Every JSON key must be in double quotes. " if NARRATION_LANG == "hi" else "")
                                            + "Always return ONLY valid JSON inside a ```json block."),
                        max_output_tokens=8192,
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
            data = validate_script(_extract_json(content), topic_spec)
            miss = sound_map.missing(data["segments"], sm)
            if miss and script_try < STORY_RETRIES:
                raise ScriptGenerationError(sound_map.missing_reason(miss))
            placed = sound_map.fill_missing(data["segments"], sm)
            if placed:
                print(f"[SoundMap] model still dropped {placed} — placed by time")
            if sm:
                data["sound_map"] = sm
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
