"""FFmpeg assembly: three slides, real timings, burned-in captions.

What changed:
  * caption timing came from len(words) * 0.4 and slide durations were
    hardcoded frame counts, so captions drifted and -shortest cut the video
    wherever the voiceover happened to end. Both now derive from the real
    measured duration of the rendered voiceover.
  * a missing slide_2/slide_3 silently became "slide 1, three times", and a
    failed filtergraph silently fell through to a single static image. Both
    now raise.
"""
import json
import os
import subprocess
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_errors import AssetGenerationError  # noqa: E402

load_dotenv()

FPS = 25
LEAD_IN = 0.25
W, H = 1080, 1920
# One caption cue per script segment meant up to 32 words on screen at once,
# which wraps to six lines and covers the slide. Cues are split at clause
# boundaries so each one is a readable phrase.
MAX_CUE_WORDS = 11
# A 1-word chunk like "Jharkhand." would flash for 0.6s; fold it into a neighbour.
MIN_CUE_WORDS = 4
MERGE_CEILING_WORDS = 14


def _bin(name, fallback):
    path = f"/opt/homebrew/bin/{name}"
    return path if os.path.exists(path) else fallback


FFMPEG_BIN = _bin("ffmpeg", "ffmpeg")
FFPROBE_BIN = _bin("ffprobe", "ffprobe")


def probe_duration(path):
    """Actual media duration in seconds. Raises if ffprobe can't read it."""
    res = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise AssetGenerationError(f"ffprobe failed on {path}: {res.stderr.strip()[:300]}")
    try:
        duration = float(json.loads(res.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise AssetGenerationError(f"Could not read a duration from {path}: {e}") from e
    if duration <= 0:
        raise AssetGenerationError(f"{path} reports a duration of {duration}s")
    return duration


def split_cue_text(text, max_words=MAX_CUE_WORDS):
    """Break one spoken line into readable caption phrases.

    Splits on sentence ends first, then on commas, then on a hard word count,
    so a caption is never a six-line wall over the slide. Pure function.
    """
    import re

    text = " ".join(str(text).split())
    if not text:
        return []
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]

    chunks = []
    for sentence in sentences:
        if len(sentence.split()) <= max_words:
            chunks.append(sentence)
            continue
        parts = [x.strip() for x in re.split(r"(?<=,)\s+", sentence) if x.strip()]
        buf = []
        for part in parts:
            if len(part.split()) > max_words:
                if buf:
                    chunks.append(" ".join(buf))
                    buf = []
                words = part.split()
                for i in range(0, len(words), max_words):
                    chunks.append(" ".join(words[i:i + max_words]))
            elif len(" ".join(buf + [part]).split()) <= max_words:
                buf.append(part)
            else:
                if buf:
                    chunks.append(" ".join(buf))
                buf = [part]
        if buf:
            chunks.append(" ".join(buf))
    return _merge_stubs(chunks) or [text]


def _merge_stubs(chunks, min_words=MIN_CUE_WORDS, ceiling=MERGE_CEILING_WORDS):
    """Fold very short chunks into a neighbour so no caption just flashes."""
    out = list(chunks)
    i = 0
    while i < len(out):
        if len(out[i].split()) >= min_words or len(out) == 1:
            i += 1
            continue
        after = len(out[i + 1].split()) if i + 1 < len(out) else 99
        before = len(out[i - 1].split()) if i > 0 else 99
        if after <= before and len(out[i].split()) + after <= ceiling:
            out[i] = f"{out[i]} {out[i + 1]}"
            del out[i + 1]
        elif i > 0 and len(out[i].split()) + before <= ceiling:
            out[i - 1] = f"{out[i - 1]} {out[i]}"
            del out[i]
            i -= 1
        else:
            i += 1
    return out


def plan_timings(segments, audio_duration, lead_in=LEAD_IN):
    """Map the script's planned segment lengths onto the real audio.

    The script plans e.g. 4/12/15/14s. If the voiceover actually runs 51.3s,
    every segment is scaled by the same factor so the captions stay aligned
    with what is being said, instead of being estimated from word count. Each
    segment is then split into short cues, weighted by word count within the
    segment, so the timing stays anchored to the segment boundaries.

    Returns a list of (start, end, text). Pure function.
    """
    usable = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        seconds = max(float(seg.get("seconds", 0) or 0), 0.0)
        for chunk in split_cue_text(text):
            share = len(chunk.split()) / max(len(text.split()), 1)
            usable.append((chunk, seconds * share))
    if not usable:
        raise AssetGenerationError("No segments with spoken text — cannot time captions")

    speech = max(audio_duration - lead_in, 0.5)
    planned_total = sum(sec for _, sec in usable)
    if planned_total <= 0:
        # No planned lengths: weight by spoken length instead of assuming equal.
        weights = [max(len(t.split()), 1) for t, _ in usable]
    else:
        weights = [sec for _, sec in usable]
    total_w = sum(weights)

    cues, t = [], lead_in
    for (text, _), w in zip(usable, weights):
        dur = speech * (w / total_w)
        cues.append((round(t, 3), round(t + dur, 3), text))
        t += dur
    # Absorb float drift into the final cue so captions end with the audio.
    start, _, text = cues[-1]
    cues[-1] = (start, round(audio_duration, 3), text)
    return cues


def plan_slide_durations(segments, audio_duration, slides=3, lead_in=LEAD_IN):
    """Split the timeline across the slides on SEGMENT boundaries.

    Slide 1 carries the hook, the last slide carries the closing segment, and
    the middle slide carries everything between. Keyed to segments, not caption
    cues — cues are sub-segment phrases, so timing off them gave the last slide
    a 2s sliver.

    Returns one duration per slide, summing to audio_duration. Pure function.
    """
    if slides < 1:
        raise ValueError("slides must be >= 1")

    weights = [
        max(float(s.get("seconds", 0) or 0), 0.0)
        for s in segments
        if str(s.get("text", "")).strip()
    ]
    if not weights or sum(weights) <= 0:
        weights = [1.0] * max(len(weights), slides)

    speech = max(audio_duration - lead_in, 0.5)
    total_w = sum(weights)
    scaled = [speech * (w / total_w) for w in weights]
    scaled[0] += lead_in  # the lead-in belongs to the first slide

    if len(scaled) <= slides:
        out = scaled + [0.6] * (slides - len(scaled))
    else:
        out = [scaled[0], sum(scaled[1:-1]), scaled[-1]]
        if slides != 3:  # generalise: first, evenly-split middle, last
            middle = out[1] / (slides - 2)
            out = [out[0]] + [middle] * (slides - 2) + [out[2]]

    out = [round(max(d, 0.6), 3) for d in out]
    # Absorb rounding into the middle slide so the total still matches.
    drift = round(audio_duration - sum(out), 3)
    mid = len(out) // 2
    out[mid] = round(out[mid] + drift, 3)
    return out


def format_srt_time(seconds):
    seconds = max(float(seconds), 0.0)
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    return f"{int(hrs):02d}:{int(mins):02d}:{int(secs):02d},{int(round((secs % 1) * 1000)):03d}"


def write_srt(cues, srt_path):
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, (start, end, text) in enumerate(cues, 1):
            f.write(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n\n")
    if os.path.getsize(srt_path) == 0:
        raise AssetGenerationError(f"Wrote an empty subtitle file: {srt_path}")
    print(f"[VideoAssembler] {len(cues)} captions -> {srt_path}")
    return srt_path


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},&H0014EBFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,6,2,2,{ml},{mr},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds):
    seconds = max(float(seconds), 0.0)
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    return f"{int(hrs)}:{int(mins):02d}:{int(secs):02d}.{int(round((secs % 1) * 100)):02d}"


def write_ass(cues, ass_path, font="Arial", size=70, margin_v=250, margin_x=100):
    """Write the captions as ASS with an explicit PlayRes.

    ffmpeg's `subtitles` filter converts SRT into an ASS script with a
    384x288 PlayRes, so a force_style Fontsize is scaled by ~6.7x and a
    bottom-aligned caption lands on top of the title. Declaring PlayRes
    ourselves makes every number below a real pixel value.
    """
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER.format(
            w=W, h=H, font=font, size=size, ml=margin_x, mr=margin_x, mv=margin_v,
        ))
        for start, end, text in cues:
            body = str(text).replace("\\", "").replace("\n", " ").strip()
            f.write(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{body}\n"
            )
    if os.path.getsize(ass_path) == 0:
        raise AssetGenerationError(f"Wrote an empty ASS file: {ass_path}")
    return ass_path


def _escape_filter_path(path):
    """ffmpeg filtergraph escaping — colons and backslashes break the graph."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def assemble_video(cover_image_path, audio_path, script_data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    topic_id = script_data.get("topic_id") or "reel"
    final_video_path = os.path.join(output_dir, f"{topic_id}_reel.mp4")
    srt_path = os.path.join(output_dir, "subtitles.srt")

    slides = [
        cover_image_path,
        os.path.join(output_dir, "slide_2_infographic.jpg"),
        os.path.join(output_dir, "slide_3_summary.jpg"),
    ]
    missing = [p for p in slides if not os.path.exists(p)]
    if missing:
        raise AssetGenerationError(
            "Missing slides, so the video would be one static image repeated: "
            + ", ".join(os.path.basename(p) for p in missing)
        )
    if not os.path.exists(audio_path):
        raise AssetGenerationError(f"Voiceover not found at {audio_path}")

    audio_duration = probe_duration(audio_path)
    cues = plan_timings(script_data.get("segments", []), audio_duration)
    write_srt(cues, srt_path)
    ass_path = write_ass(cues, os.path.join(output_dir, "subtitles.ass"))
    durations = plan_slide_durations(
        script_data.get("segments", []), audio_duration, slides=len(slides)
    )
    print(
        f"[VideoAssembler] audio {audio_duration:.2f}s -> slides "
        + " / ".join(f"{d:.1f}s" for d in durations)
    )

    parts, labels = [], []
    for i, dur in enumerate(durations):
        frames = max(int(round(dur * FPS)), 1)
        zoom = 1.15 if i != 1 else 1.10
        step = (zoom - 1.0) / frames
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"zoompan=z='min(zoom+{step:.6f},{zoom})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:fps={FPS}:s={W}x{H},setpts=PTS-STARTPTS[v{i}];"
        )
        labels.append(f"[v{i}]")
    filter_graph = (
        "".join(parts)
        + f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vcat];"
        + f"[vcat]ass='{_escape_filter_path(ass_path)}'[vout]"
    )

    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error"]
    for path in slides:
        # -framerate 1 -loop 1 -t 1 feeds zoompan EXACTLY ONE frame, so its
        # d=<frames> is the whole clip length. With a plain `-loop 1` the image
        # stream is infinite, zoompan emits d frames per input frame forever,
        # and concat never advances past the first slide — the video is slide 1
        # for its whole duration, which is what the earlier renders were.
        cmd += ["-framerate", "1", "-loop", "1", "-t", "1", "-i", path]
    cmd += [
        "-i", audio_path,
        "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", f"{len(slides)}:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-t", f"{audio_duration:.3f}",
        final_video_path,
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(final_video_path):
        raise AssetGenerationError(
            f"ffmpeg assembly failed (exit {res.returncode}).\n{res.stderr.strip()[:900]}"
        )

    rendered = probe_duration(final_video_path)
    if abs(rendered - audio_duration) > 1.5:
        raise AssetGenerationError(
            f"Rendered video is {rendered:.1f}s but the voiceover is {audio_duration:.1f}s — "
            f"the timeline did not cover the audio"
        )

    print(
        f"[VideoAssembler] {os.path.basename(final_video_path)} — "
        f"{rendered:.1f}s, {os.path.getsize(final_video_path) / 1e6:.1f} MB"
    )
    return final_video_path


# Kept for callers that imported the old name.
def create_srt_file(script_data, srt_path, audio_duration=None):
    duration = audio_duration or sum(
        float(s.get("seconds", 0) or 0) for s in script_data.get("segments", [])
    )
    return write_srt(plan_timings(script_data.get("segments", []), duration), srt_path)
