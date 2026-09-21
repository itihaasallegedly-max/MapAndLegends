"""Assemble the six illustrations and the Kokoro narration into one reel.

Image durations come from the measured per-scene audio, so a picture changes
exactly when the narration moves on.
"""
import json
import math
import re
import subprocess

W, H, FPS = 1080, 1920, 25
MAX_CUE_WORDS = 11
MIN_CUE_WORDS = 4
MERGE_CEILING = 14

d = json.load(open("story.json"))
marks = d["scene_marks"]
AUDIO = "audio/narration.wav"
TOTAL = float(subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", AUDIO],
    capture_output=True, text=True).stdout.strip())


# ---------------------------------------------------------------- captions
def split_cue_text(text, max_words=MAX_CUE_WORDS):
    text = " ".join(str(text).split())
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    chunks = []
    for sentence in sentences:
        if len(sentence.split()) <= max_words:
            chunks.append(sentence)
            continue
        parts = [x.strip() for x in re.split(r"(?<=[,:;])\s+", sentence) if x.strip()]
        buf = []
        for part in parts:
            if len(part.split()) > max_words:
                if buf:
                    chunks.append(" ".join(buf)); buf = []
                w = part.split()
                for i in range(0, len(w), max_words):
                    chunks.append(" ".join(w[i:i + max_words]))
            elif len(" ".join(buf + [part]).split()) <= max_words:
                buf.append(part)
            else:
                if buf:
                    chunks.append(" ".join(buf))
                buf = [part]
        if buf:
            chunks.append(" ".join(buf))
    # fold stubs so nothing flashes
    out, i = list(chunks), 0
    while i < len(out):
        if len(out[i].split()) >= MIN_CUE_WORDS or len(out) == 1:
            i += 1; continue
        after = len(out[i + 1].split()) if i + 1 < len(out) else 99
        before = len(out[i - 1].split()) if i > 0 else 99
        if after <= before and len(out[i].split()) + after <= MERGE_CEILING:
            out[i] = f"{out[i]} {out[i+1]}"; del out[i + 1]
        elif i > 0 and len(out[i].split()) + before <= MERGE_CEILING:
            out[i - 1] = f"{out[i-1]} {out[i]}"; del out[i]; i -= 1
        else:
            i += 1
    return out


cues = []
for m in marks:
    chunks = split_cue_text(m["text"])
    span = m["end"] - m["start"]
    weights = [len(c.split()) for c in chunks]
    tw = sum(weights)
    t = m["start"]
    for c, wgt in zip(chunks, weights):
        dur = span * wgt / tw
        cues.append((round(t, 3), round(t + dur, 3), c))
        t += dur

ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,66,&H00DFEFF7,&H000000FF,&H00201408,&H90000000,-1,0,0,0,100,100,0,0,1,5,2,2,110,110,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_t(sec):
    h, rem = divmod(max(sec, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{int(s):02d}.{int(round((s % 1) * 100)):02d}"


with open("captions.ass", "w", encoding="utf-8") as f:
    f.write(ASS_HEAD)
    for a, b, txt in cues:
        f.write(f"Dialogue: 0,{ass_t(a)},{ass_t(b)},Cap,,0,0,0,,{txt}\n")
print(f"  {len(cues)} caption cues -> captions.ass")

# ---------------------------------------------------------------- filtergraph
# Alternating slow push / pull so six stills do not feel mechanical.
MOVES = [
    ("in",  1.10, "up"),
    ("out", 1.12, "left"),
    ("in",  1.12, "down"),
    ("in",  1.09, "right"),
    ("out", 1.11, "up"),
    ("in",  1.10, "left"),
]

parts, labels, inputs = [], [], []
for i, m in enumerate(marks):
    start = m["start"] if i else 0.0
    end = marks[i + 1]["start"] if i + 1 < len(marks) else TOTAL
    
    start_frame = int(round(start * FPS))
    if i + 1 == len(marks):
        end_frame = int(math.ceil(TOTAL * FPS)) + FPS  # add 1s padding to the last scene
    else:
        end_frame = int(round(end * FPS))
        
    dur = end - start
    frames = max(end_frame - start_frame, 2)
    mode, zmax, drift = MOVES[i % len(MOVES)]
    if mode == "in":
        z = f"min(zoom+{(zmax - 1) / frames:.7f},{zmax})"
        base = 1.0
    else:
        z = f"if(eq(on,1),{zmax},max(zoom-{(zmax - 1) / frames:.7f},1.0))"
        base = zmax
    span = 0.5
    if drift == "up":
        x, y = "iw/2-(iw/zoom/2)", f"ih/2-(ih/zoom/2)-(on/{frames})*{60 * span:.0f}"
    elif drift == "down":
        x, y = "iw/2-(iw/zoom/2)", f"ih/2-(ih/zoom/2)+(on/{frames})*{60 * span:.0f}"
    elif drift == "left":
        x, y = f"iw/2-(iw/zoom/2)-(on/{frames})*{50 * span:.0f}", "ih/2-(ih/zoom/2)"
    else:
        x, y = f"iw/2-(iw/zoom/2)+(on/{frames})*{50 * span:.0f}", "ih/2-(ih/zoom/2)"

    inputs += ["-framerate", "1", "-loop", "1", "-t", "1", "-i", f"img/scene_{i+1}.png"]
    parts.append(
        f"[{i}:v]scale={int(W*1.5)}:{int(H*1.5)}:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:fps={FPS}:s={W}x{H},"
        f"setpts=PTS-STARTPTS[v{i}];"
    )
    labels.append(f"[v{i}]")
    print(f"  scene {i+1}: {start:6.2f}-{end:6.2f}s  {dur:5.2f}s  {frames:4d}f  {mode} {drift}")

n = len(marks)
t_out = marks[0]["end"] + 0.15
# These two overlays must be LOOPED, TIMED streams. A bare `-i x.png` is a
# single frame at t=0, so a fade filter evaluates once, at t=0, and holds that
# result forever — an alpha fade-in leaves the overlay invisible for the whole
# video.
inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{TOTAL + 1.0:.3f}", "-i", "img/scrim.png"]
inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{t_out:.3f}", "-i", "img/title.png"]
inputs += ["-i", AUDIO]
SCRIM, TITLE, AUD = n, n + 1, n + 2

graph = "".join(parts)
graph += "".join(labels) + f"concat=n={n}:v=1:a=0[cat];"
# a whole-frame fade up from black at the head and out at the tail
graph += f"[cat]fade=t=in:st=0:d=0.6,fade=t=out:st={TOTAL-0.7:.2f}:d=0.7[faded];"
graph += f"[{SCRIM}:v]scale={W}:{H}[scrim];[faded][scrim]overlay=0:0[withscrim];"
# title card, fading in and out over the first scene
graph += (f"[{TITLE}:v]scale={W}:{H},format=rgba,"
          f"fade=t=in:st=0.35:d=0.7:alpha=1,fade=t=out:st={t_out-0.8:.2f}:d=0.8:alpha=1[title];")
graph += f"[withscrim][title]overlay=0:0:enable='between(t,0,{t_out:.2f})'[titled];"
graph += "[titled]ass=captions.ass[vout]"

cmd = (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
       ["-filter_complex", graph,
        "-map", "[vout]", "-map", f"{AUD}:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-t", f"{TOTAL:.3f}",
        "jharkhand_story_reel.mp4"])

print(f"  rendering {TOTAL:.2f}s ...")
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(res.stderr[-2500:])
    raise SystemExit(res.returncode)
print("  done -> jharkhand_story_reel.mp4")
