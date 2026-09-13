"""Render one video's whole script into a single plain-text file.

The pipeline's script-only mode stops here: no voice, no visuals, no render.
What comes out is a production sheet to work from by hand — narration to paste
into a text-to-speech engine, video-generation prompts split into clips your
model will actually accept, and the fact-check verdicts behind the claims.

Two files are written side by side:
  <topic>.txt    the sheet, for a person
  <topic>.json   the same data, for a machine
"""
import datetime
import json
import math
import os
import textwrap

from dotenv import load_dotenv

import naming

load_dotenv()

WRAP = 74
RULE = "=" * WRAP
THIN = "-" * WRAP

DEFAULT_STYLE = (
    "vintage cartography documentary style, deep navy and antique gold with "
    "cream, warm cinematic light, film grain, no text, no captions, no logos, "
    "no watermark, no on-screen writing, vertical 9:16"
)


def video_style():
    # IMAGE_STYLE is the older name; still honoured so an existing .env works.
    return (os.getenv("VIDEO_STYLE") or os.getenv("IMAGE_STYLE")
            or DEFAULT_STYLE).strip()


def clip_seconds():
    try:
        return max(float(os.getenv("CLIP_SECONDS", "8")), 1.0)
    except ValueError:
        return 8.0


def scene_video_prompt(seg, subject, style=None):
    core = str(seg.get("video_prompt") or seg.get("image_prompt")
               or seg.get("visual") or "").strip()
    if not core:
        core = f"a slow drifting shot of {subject}"
    return f"{core}. {style or video_style()}"


def split_into_clips(duration, clip_len):
    """Model-sized clips that still add up to the scene.

    A 16s scene cannot be one generation on most text-to-video models, so it
    becomes two 8s clips rather than a prompt you cannot actually run.
    """
    n = max(1, math.ceil(round(duration, 3) / clip_len))
    # Round the BOUNDARIES, not each length: rounding lengths independently
    # lets the error accumulate (17s in three clips came to 17.01s).
    bounds = [round(duration * i / n, 2) for i in range(n)] + [round(duration, 2)]
    return [(bounds[i], bounds[i + 1], round(bounds[i + 1] - bounds[i], 2))
            for i in range(n)]


def timecode(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def _block(text, indent="  ", width=WRAP):
    return textwrap.fill(" ".join(str(text).split()), width=width,
                         initial_indent=indent, subsequent_indent=indent)


def build(script, topic_spec, gate, channel=None):
    """Return (text, payload). Pure — no filesystem, unit-testable."""
    channel = channel or os.getenv("CHANNEL_NAME", "Map & Legend")
    subject = topic_spec.get("subject") or topic_spec.get("topic", "")
    segs = script.get("segments", [])
    style = video_style()
    clip_len = clip_seconds()

    scenes, t, total_clips = [], 0.0, 0
    for i, seg in enumerate(segs, 1):
        dur = float(seg.get("seconds", 0) or 0)
        text = str(seg.get("text", "")).strip()
        words = len(text.split())
        clips = split_into_clips(dur, clip_len)
        total_clips += len(clips)
        scenes.append({
            "scene": i,
            "start": round(t, 2),
            "end": round(t + dur, 2),
            "seconds": dur,
            "narration": text,
            "words": words,
            "wpm": round(words / (dur / 60)) if dur else None,
            "video_prompt": scene_video_prompt(seg, subject, style),
            "clips": [{"clip": k + 1, "of": len(clips),
                       "start": round(t + a, 2), "end": round(t + b, 2),
                       "seconds": d} for k, (a, b, d) in enumerate(clips)],
        })
        t += dur

    total = t
    all_words = sum(s["words"] for s in scenes)
    narration = " ".join(s["narration"] for s in scenes)
    verdict = "PASS" if gate.get("safe_to_publish") else "HOLD"

    L = []
    a = L.append
    a(RULE)
    a(channel.upper())
    a(str(script.get("title", subject)).upper())
    a(RULE)
    a(f"Subject      {subject}")
    a(f"Topic        {topic_spec.get('topic', '')}")
    a(f"Series       {topic_spec.get('series', '')}")
    if script.get("title_regional"):
        a(f"Local title  {script['title_regional']}")
    a(f"Runtime      {total:.0f}s across {len(scenes)} scenes")
    if total:
        a(f"Pace         {all_words} words, {all_words / (total / 60):.0f} wpm")
    a(f"Clips        {total_clips} generations at {clip_len:.0f}s each")
    a(f"Fact check   {verdict} - {gate.get('reason', '')}")
    a(f"Generated    {datetime.datetime.now().isoformat(timespec='seconds')}")
    a("")
    a("HOW TO USE THIS")
    a(_block("Record the narration first — the FULL NARRATION block at the "
             "bottom goes into your text-to-speech engine in one pass. Then "
             "generate the video clips below and cut them to the scene "
             "timings. The timings here are planned; once you have the real "
             "audio, trust its length over these numbers."))
    a("")
    a(_block(f"Every generation comes out at your model's full clip length "
             f"(currently {clip_len:.0f}s). Where a scene needs more than one, "
             f"each clip below shows the slice of timeline it has to cover — "
             f"trim the generation down to that."))
    a("")

    for s in scenes:
        a(THIN)
        a(f"SCENE {s['scene']}   {timecode(s['start'])}-{timecode(s['end'])}   "
          f"{s['seconds']:.0f}s   {len(s['clips'])} clip"
          f"{'s' if len(s['clips']) > 1 else ''}")
        a(THIN)
        a("")
        a(f"NARRATION   {s['words']} words"
          + (f", {s['wpm']} wpm" if s["wpm"] else ""))
        a(_block(s["narration"]))
        a("")
        if len(s["clips"]) == 1:
            a(f"VIDEO PROMPT   {s['clips'][0]['seconds']:.0f}s")
            a(_block(s["video_prompt"]))
        else:
            a(f"VIDEO PROMPT   one continuous shot across "
              f"{len(s['clips'])} clips")
            a(_block(s["video_prompt"]))
            a("")
            a(_block("Generate each clip below from that same prompt, then "
                     "join them. For clips after the first, append the "
                     "continuation line so the motion carries through.",
                     indent="  "))
            for c in s["clips"]:
                a("")
                a(f"  clip {c['clip']}/{c['of']}   covers "
                  f"{timecode(c['start'])}-{timecode(c['end'])}   "
                  f"trim to {c['seconds']:.1f}s")
                if c["clip"] > 1:
                    a(_block("+ continues the previous shot without a cut, "
                             "the camera keeps moving in the same direction",
                             indent="    "))
        a("")

    a(RULE)
    a("FULL NARRATION   paste into your text-to-speech engine in one go")
    a(RULE)
    a("")
    a(textwrap.fill(narration, width=WRAP))
    a("")
    a(RULE)
    a("POST CAPTION")
    a(RULE)
    a("")
    a(textwrap.fill(" ".join(str(script.get("caption", "")).split()), width=WRAP))
    a("")
    a(RULE)
    a(f"FACT CHECK   {verdict} - {gate.get('reason', '')}")
    a(RULE)
    a(f"Checked by: {gate.get('gate_model', 'n/a')}")
    a("")
    for i, v in enumerate(gate.get("verdicts", []), 1):
        a(f"{i:>2}. [{str(v.get('status', '')).upper()}]")
        a(_block(v.get("claim", ""), indent="    "))
        if v.get("note"):
            a(_block(f"source: {v['note']}", indent="    "))
        a("")

    a(RULE)
    a("BEFORE YOU PUBLISH")
    a(RULE)
    a("  [ ] Every scene has footage")
    a("  [ ] Narration recorded and cut to the scene lengths")
    a("  [ ] Captions burned in and readable on a phone")
    a("  [ ] Channel handle correct in the footer")
    a("  [ ] Runtime between 45 and 60 seconds")
    a("")

    payload = {
        "channel": channel,
        "title": script.get("title"),
        "title_regional": script.get("title_regional"),
        "subject": subject,
        "topic": topic_spec.get("topic"),
        "series": topic_spec.get("series"),
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "planned_seconds": round(total, 2),
        "words": all_words,
        "clip_seconds": clip_len,
        "total_clips": total_clips,
        "video_style": style,
        "scenes": scenes,
        "full_narration": narration,
        "caption": script.get("caption"),
        "facts": script.get("facts", []),
        "fact_check": gate,
    }
    return "\n".join(L), payload


def write(script, topic_spec, gate, output_dir, channel=None, slug=None):
    os.makedirs(output_dir, exist_ok=True)
    txt, payload = build(script, topic_spec, gate, channel)
    slug = slug or naming.slugify(topic_spec.get("topic", ""))
    txt_path = naming.path(output_dir, slug, "sheet")
    js_path = naming.path(output_dir, slug, "data")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[ScriptSheet] {len(payload['scenes'])} scenes, "
          f"{payload['total_clips']} clips, {payload['planned_seconds']:.0f}s "
          f"-> {txt_path}")
    return txt_path, js_path
