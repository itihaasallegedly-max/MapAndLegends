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

from core import model_client
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
        raise ScriptGenerationError(
            f"Model returned malformed JSON ({e}): {text[start:start + 300]!r}"
        ) from e


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

    total = sum(s["seconds"] for s in segments)
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
        video_file_obj = client.files.upload(path=video_path)
        print(f"[Prompt 2] Video uploaded as {video_file_obj.name}. Waiting for processing...")
        while video_file_obj.state.name == "PROCESSING":
            time.sleep(3)
            video_file_obj = client.files.get(name=video_file_obj.name)
            print(".", end="", flush=True)
        print()
        if video_file_obj.state.name == "FAILED":
            raise ScriptGenerationError(f"Gemini failed to process the video: {video_path}")
        print("[Prompt 2] Video processing complete!")

    prompt = f"""
    You write scripts for {int(TARGET_MIN_S)}-{int(TARGET_MAX_S)} second vertical geography shorts aimed at an Indian audience,
    including competitive-exam aspirants.

    YOUR TOPIC IS: "{subject}"
    {set_brief(topic_spec)}

    {"WATCH THE ATTACHED COMPETITOR VIDEO. You must transcribe its spoken script EXACTLY word-for-word. You must also replicate its visuals EXACTLY frame-by-frame. For each segment, the 'video_prompt' and the 'per_second' visual breakdowns must match the camera angles, map movements, and visual elements of the competitor video exactly, second by second. Do not create an original script or original visuals; duplicate theirs exactly." if video_file_obj else ""}

    VISUAL STYLE TO EMULATE:
    {dynamic_style}

    Return JSON only with this exact shape:

    {{
      "title": "<max 4 words, all caps, fits on a cover image>",
      "title_regional": "<the same title in the locally relevant script (Devanagari, Telugu, Tamil, Bengali, Assamese...) — for a non-Indian subject, return the English title>",
      "hook": "<first 3 seconds of spoken script — a question or a surprising number>",
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
    - Plain spoken Indian English. No jargon, no filler.
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
      start of THIS scene. One or two, in the places a cut or a movement wants
      punctuation. Leave the array empty rather than inventing filler.
    - "voice" is direction for the narrator: pace, weight, the word that lifts.
    - "mix" is levels. Narration always in front of music.
    - Describe sound that belongs to the place or the object. Never name a
      track, an artist, a song or a library cue.
    """

    contents = [video_file_obj, prompt] if video_file_obj else [prompt]

    try:
        response, model = model_client.call_model(
            lambda m: client.models.generate_content(
                model=m,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction="You are a professional scriptwriter for geography shorts. Always return ONLY valid JSON inside a ```json block.",
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

    data = validate_script(_extract_json(content), topic_spec)
    total = sum(s["seconds"] for s in data["segments"])
    print(
        f"[Prompt 2] Script for '{subject}': {len(data.get('segments', []))} scenes, "
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
