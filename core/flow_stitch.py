"""Script segments -> Google Flow clips on disk -> one reel, stitched locally.

Same shape as ItihaasaAllegedly's make_itihaas_video.flow_scenes():

  1. build_job()   one Flow prompt per segment (shot, beats, style, sound, and the
                   exact narration line, so Flow voices it), written as a job file
  2. run_flow()    core/flow_gen.py queues them all, waits, downloads each clip
  3. stitch()      every clip normalised to 1080x1920 / 30 fps, audio pulled out of
                   each, concatenated, loudness-normalised, optional brand bed,
                   muxed into outputs/<slug>/<slug>_reel.mp4 and checked

Downloaded clips are kept under outputs/<slug>/flow/ across re-runs: they cost
Flow credits, and a crashed run should resume, not regenerate.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

import naming  # noqa: E402
from pipeline_errors import AssetGenerationError  # noqa: E402
from utils.audio_estimation import estimate_audio_duration  # noqa: E402

W, H, FPS = 1080, 1920, 30
# Every reel is made in each of these, as its own Flow batch: 9:16 for Shorts
# and Reels, 16:9 as a regular YouTube video and an Instagram post.
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
ASPECTS = [a.strip() for a in os.getenv("ASPECTS", "9:16,16:9").split(",") if a.strip() in SIZES] or ["9:16"]
FLOW_MAX_S = 10.0
FLOW_TIMEOUT = int(os.getenv("FLOW_RUN_TIMEOUT", "3600"))   # whole batch, seconds
NARRATION_LANG = os.getenv("NARRATION_LANG", "hi").strip().lower()
# Every clip is voiced by a fresh generation, so "a warm narrator" produced a
# different man in each scene (median pitch 110-208 Hz across one reel). The
# voice is therefore pinned down as far as words can: the same description,
# word for word, in every clip, and no per-scene "tone" that would move it.
VOICE = os.getenv("FLOW_VOICE",
    "the SAME single narrator in every clip of this video: one adult Indian man in "
    "his late thirties, deep baritone, low steady pitch, calm unhurried documentary "
    "delivery at an even pace of about two and a half words per second, neutral "
    "Hindi accent, close studio microphone, dry with no echo. Never a woman, a child, "
    "a second voice, a high or excited voice, whispering or shouting"
    if NARRATION_LANG == "hi" else
    "the SAME single narrator in every clip of this video: one adult Indian man in "
    "his late thirties, deep baritone, low steady pitch, calm unhurried documentary "
    "delivery, close studio microphone, dry with no echo")
# Measured on the published Cyprus reel: Flow's narrator averaged 2.5 words/s
# and stretched the line to fill whatever length it was given (2.1-3.1 w/s).
FLOW_WORDS_PER_S = float(os.getenv("FLOW_WORDS_PER_S", "2.4"))
GRID = (4, 6, 8, 10)
# FLOW_MUSIC=1: Flow generates soft music inside every clip, and the local bed
# is skipped so the two never stack. Each clip is a separate generation, so the
# music line is fixed and identical in every prompt; expect some drift at cuts.
# Clips go to flow_music/ so they never mix with (or reuse) music-less clips.
FLOW_MUSIC = os.getenv("FLOW_MUSIC", "0").strip().lower() in ("1", "true", "yes", "on")
MUSIC_STYLE = os.getenv("FLOW_MUSIC_STYLE",
    "one slow, calm ambient string pad in a minor key with a soft low piano note now and then, "
    "no drums, no beat, no melody that competes with the voice, the SAME music in every clip")
NEG = ("full-bleed edge-to-edge {orient} frame, no border, no letterbox, no on-screen "
       "text, no map labels, no letters or numbers anywhere, no captions, no subtitles, "
       "no watermark, no logos")


def log(*a):
    print("[stitch]", *a, flush=True)


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssetGenerationError(f"{cmd[0]} failed: {r.stderr.strip()[-600:]}")
    return r.stdout


def probe_duration(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip() or 0)


def has_audio(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", str(path)])
    return "audio" in out


# ------------------------------------------------------------------ prompts

def spoken_line(seg):
    """What Flow must say: the script's own narration (written in Hindi when NARRATION_LANG=hi)."""
    return str(seg.get("text") or "").strip()


def clip_seconds(seg):
    """The shortest 4/6/8/10s clip the line fits in, with half a second spare.

    It used to be the scene's planned length, rounded up again by Flow's chip:
    an 8-second clip for a 5-second line, so Flow either padded the start with
    silence and the end with ambience, or slowed the narrator to fill it. Both
    read as a gap at every cut."""
    words = len(spoken_line(seg).split())
    if not words:
        return float(seg.get("seconds") or 6)
    need = words / FLOW_WORDS_PER_S + 0.5
    for g in GRID:
        if need <= g:
            return float(g)
    return float(GRID[-1])


def fit_beats(beats, n):
    """Exactly n per-second beats: keep the first and last, sample evenly between."""
    beats = [str(b).strip() for b in beats if str(b).strip()]
    if len(beats) <= n or n < 2:
        return beats[:max(n, 0)] if n < 2 else beats
    step = (len(beats) - 1) / (n - 1)
    return [beats[round(i * step)] for i in range(n)]


def _position(i, n):
    if n <= 1:
        return "This is the whole narration."
    if i == 1:
        return (f"This is clip 1 of {n} of ONE continuous narration: it opens the video, "
                "so the line is delivered as a hook, straight in, with no greeting.")
    if i == n:
        return (f"This is clip {n} of {n}, the last clip of ONE continuous narration: "
                "the narrator continues from the previous clip and closes on a calm, final tone.")
    return (f"This is clip {i} of {n} of ONE continuous narration: the narrator is mid-story, "
            "continuing from the previous clip into the next one. No greeting, no introduction, "
            "no pause to start, and no closing or final tone at the end.")


def build_prompt(seg, style="", index=1, total=1, seconds=None, music_style=None, aspect="9:16"):
    """One Flow prompt. The narration block goes FIRST: text-to-video models
    weight the start of a prompt, and the narration is what has to carry across
    clips; the picture only has to carry within one."""
    seconds = int(seconds or clip_seconds(seg))
    text = spoken_line(seg)
    lang = "Hindi" if NARRATION_LANG == "hi" else "English"
    wide = aspect == "16:9"
    parts = ["Horizontal 16:9 widescreen, composed for the full width of a landscape frame."
             if wide else "Vertical 9:16.", f"{seconds}-second clip.",
             # 29 Sep: Flow's own map text came out misspelled ("Ghapra",
             # "MANFAJPUR", invented Hindi). Labels are overlaid at stitch time.
             "The picture contains NO writing at all: no letters, words, numbers, labels, "
             "place names, map text, titles or signs anywhere in the frame."]
    if text:
        speak_by = max(1.0, seconds - 0.5)
        parts.append(
            f"NARRATION: {_position(index, total)} Voice: {VOICE}. "
            f"The narrator starts speaking at 0.2 seconds, with no silent intro, speaks this {lang} "
            f"line exactly once, word for word, adding nothing before or after it, and finishes by "
            f"{speak_by:.1f} seconds: \"{text}\"")
    shot = str(seg.get("video_prompt") or "A cinematic geography shot.").strip()
    parts.append("SHOT: " + (shot if shot.endswith((".", "!", "?")) else shot + "."))
    beats = fit_beats(seg.get("per_second") or [], seconds)
    if beats:
        parts.append("Beat by beat: " + " | ".join(f"{i}s: {b}" for i, b in enumerate(beats)))
    bg = seg.get("background") or {}
    if isinstance(bg, dict) and bg.get("description"):
        parts.append(f"Background: {bg['description']}")
    if style:
        parts.append(f"Visual style: {style}.")
    # 29 Sep: "Europeans fought over the Caribbean" came out with invented
    # flags (a German/Portuguese hybrid, a Serbian-looking tricolour).
    if re.search(r"\bflags?\b", shot + " " + " ".join(map(str, beats)), re.I):
        parts.append("FLAGS: every flag is the exact, real national flag of the country it "
                     "stands for — correct colours, stripe order and emblem; no invented or "
                     "blended flags; at most three flags on screen at once, large and clear.")
    # 29 Sep: the French Guiana reel showed India's State Emblem with a garbled
    # motto, rupee notes and a passport while the line was about France.
    parts.append("SYMBOLS: any flag, national emblem, coat of arms, currency, passport or "
                 "landmark shown must be the real one of the country or place this line is "
                 "about, drawn accurately; never a symbol, currency or document of any other "
                 "country (no Indian symbols unless the line is about India).")
    if seg.get("_avoid"):
        parts.append(str(seg["_avoid"]))
    neg_prompt = NEG.format(orient="horizontal" if wide else "vertical")
    if seg.get("_cta"):
        neg_prompt = neg_prompt.replace(", no on-screen text, no captions, no subtitles, no watermark, no logos", "")
    parts.append(neg_prompt + ".")
    audio = seg.get("audio") or {}
    sound, fxs = [], []
    if isinstance(audio, dict):
        amb = str(audio.get("ambience") or "").strip().rstrip(".")
        if amb:
            sound.append(amb)
        # Spot effects are the competitor's signature details (a firecracker
        # crackle, a map pop). "Far below the voice" buried them, so they get
        # their own sentence: clearly heard, timed, never over a word.
        for fx in audio.get("sfx") or []:
            if isinstance(fx, dict) and fx.get("cue"):
                at = min(float(fx.get("at", 0) or 0), max(0.0, seconds - 0.5))
                fxs.append(f"{str(fx['cue']).strip().rstrip('.')} at {at:.1f}s")
    # Music is laid once under the whole reel at stitch time; per-clip music
    # would restart at every cut. Ambience was reaching -24 dB after the line,
    # which is what the gaps between scenes were full of.
    # A punchline voice (crowd shouting, a choir, a meme clip) is itself an
    # effect, so the blanket "no other voices" must not suppress it.
    vocal_fx = any(re.search(r"shout|scream|choir|crowd|cheer|laugh|voice|gasp|chant", f, re.I) for f in fxs)
    no_voices = "" if vocal_fx else "No other voices, no singing. "
    tail = ("SOUND: the narration is the main sound. "
            + (("Very quiet background: " + "; ".join(sound) + ", far below the voice. ") if sound else "")
            + (("SOUND EFFECTS, each one clearly heard at its moment, sitting just under the "
                "voice and never covering a word: " + "; ".join(fxs) + ". ") if fxs else "")
            + (f"Soft background music from 0 seconds to the end: {music_style or MUSIC_STYLE}; always much quieter "
               "than the narration, steady volume, no swell, no sudden start or stop. " + no_voices
               if FLOW_MUSIC else "No music. " + no_voices)
            + (("When the narrator finishes, only the soft music continues." if FLOW_MUSIC
                else "When the narrator finishes, the clip ends almost silent.") if text else ""))
    parts.append(tail if text else tail + " No voiceover.")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def build_job(script_data, output_dir, slug, aspect="9:16"):
    segments = script_data.get("segments") or []
    if not segments:
        raise AssetGenerationError("script has no segments")
    style = os.getenv("VIDEO_STYLE", "").strip()
    flow_dir = Path(output_dir) / (("flow_music" if FLOW_MUSIC else "flow")
                                   + ("_169" if aspect == "16:9" else ""))
    flow_dir.mkdir(parents=True, exist_ok=True)
    n = len(segments)
    clips = []
    # The competitor's music, described (never named), if the sound map heard
    # one; the same words in every clip so the generations stay close.
    mus = ((script_data.get("sound_map") or {}).get("music") or {})
    music_style = None
    if mus.get("present") and str(mus.get("style") or "").strip():
        music_style = (str(mus["style"]).strip().rstrip(".") +
                       ", no vocals, the SAME music in every clip")
    for i, seg in enumerate(segments, 1):
        secs = clip_seconds(seg)
        clips.append({"prompt": build_prompt(seg, style, index=i, total=n, seconds=secs, music_style=music_style, aspect=aspect),
                      "seconds": secs,
                      "is_cta": seg.get("_cta", False),
                      # the scene whose line asks to subscribe gets the animation
                      "subscribe": bool(re.search(r"सब्सक्राइब|subscribe", str(seg.get("text", "")), re.I)),
                      "labels": [str(x).strip() for x in (seg.get("labels") or []) if str(x).strip()][:3],
                      "line": spoken_line(seg),
                      "out": str(flow_dir / f"{i:02d}.mp4")})
    job_path = Path(naming.path(output_dir, slug, "flow_job"))
    if aspect == "16:9":
        job_path = job_path.with_name(job_path.name.replace("_flow_job", "_flow_job_169"))
    job_path.write_text(json.dumps({"aspect": aspect, "clips": clips},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return job_path, clips


def run_flow(job_path):
    """Run the Flow downloader in its own process (a hung browser can be killed)."""
    # The batch timeout grows with the clip count — a long remake is not a hang.
    try:
        n = len(json.loads(Path(job_path).read_text(encoding="utf-8")).get("clips") or [])
    except (OSError, ValueError):
        n = 0
    timeout = max(FLOW_TIMEOUT, 300 * n)
    try:
        rc = subprocess.run([sys.executable, str(BASE_DIR / "core" / "flow_gen.py"), str(job_path)],
                            cwd=BASE_DIR, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        log(f"flow_gen exceeded {timeout}s — downloaded clips are kept for the next run")
        return 1
    return rc


# ------------------------------------------------------------------ stitch

def _handle_overlay(path, size=(W, H)):
    """A small channel-handle footer, or None if there is no handle / no Pillow."""
    handle = os.getenv("CHANNEL_HANDLE", "").strip()
    if not handle:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = None
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(f):
            font = ImageFont.truetype(f, 34)
            break
    font = font or ImageFont.load_default()
    tw = d.textlength(handle, font=font)
    d.text(((w - tw) / 2, h - (120 if h > w else 80)), handle, font=font, fill=(217, 169, 76, 235),
           stroke_width=3, stroke_fill=(10, 23, 40, 200))
    img.save(path)
    return path


def _label_overlay(path, labels, size=(W, H), base=None):
    """The handle footer (if any) plus up to three label pills near the top,
    drawn by us so place names and numbers are spelled right. Latin letters
    only unless Pillow has raqm: without it Devanagari comes out unshaped."""
    labels = [l for l in (labels or []) if l][:3]
    if not labels:
        return base
    try:
        from PIL import Image, ImageDraw, ImageFont, features
    except ImportError:
        return base
    if not features.check("raqm"):
        labels = [l for l in labels if not re.search(r"[\u0900-\u097F]", l)]
        if not labels:
            return base
    w, h = size
    img = Image.open(base).convert("RGBA") if base else Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size_px = 58 if h > w else 46
    font = None
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Kohinoor.ttc",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(f):
            font = ImageFont.truetype(f, size_px)
            break
    font = font or ImageFont.load_default()
    y = int(h * (0.12 if h > w else 0.08))
    for text in labels:
        tw = d.textlength(text, font=font)
        pad_x, pad_y = 30, 16
        x0 = (w - tw) / 2 - pad_x
        box = (x0, y, x0 + tw + 2 * pad_x, y + size_px + 2 * pad_y)
        d.rounded_rectangle(box, radius=(size_px + 2 * pad_y) // 2, fill=(10, 23, 40, 205),
                            outline=(217, 169, 76, 255), width=4)
        d.text(((w - tw) / 2, y + pad_y - 4), text, font=font, fill=(242, 214, 150, 255))
        y += size_px + 2 * pad_y + 18
    img.save(path)
    return path


def _with_locator(path, base, locator, size):
    """The clip's overlay with the locator-map inset composited on top."""
    try:
        from PIL import Image
        img = Image.open(base).convert("RGBA") if base else Image.new("RGBA", size, (0, 0, 0, 0))
        loc = Image.open(locator).convert("RGBA")
        if loc.size != img.size:
            loc = loc.resize(img.size)
        img.alpha_composite(loc)
        img.save(path)
        return path
    except Exception as e:  # noqa: BLE001 — an inset is never worth a failed render
        log(f"locator inset skipped: {type(e).__name__}: {e}")
        return base


SUBSCRIBE_CLIP = os.getenv("SUBSCRIBE_CLIP", "brand/subscribe.mp4")
SUBSCRIBE_MAX_S = float(os.getenv("SUBSCRIBE_MAX_S", "5"))
SUBSCRIBE_SFX = float(os.getenv("SUBSCRIBE_SFX_VOLUME", "0.6"))


def _key_colour(clip):
    """The animation's background: sampled from the first frame's corner, so a
    black-screen and a green-screen subscribe clip both work. None = no key."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-frames:v", "1", "-vf",
                        "crop=8:8:0:0,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True)
    if len(r.stdout) < 3:
        return None
    rgb = r.stdout[:3]
    return "0x%02x%02x%02x" % (rgb[0], rgb[1], rgb[2])


def _dims(clip):
    """(width, height) of the first video stream, or (0, 0)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(clip)],
                           capture_output=True, text=True)
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 0, 0


def _with_subscribe(v, a, keep, work, k, size):
    """Overlay brand/subscribe.mp4 (a like/subscribe/bell animation on a black
    or green background) over the END of the clip whose line asks the viewer to
    subscribe — the spoken line comes last in that scene — and mix its click
    sound under the narration. No file, or any failure: the clip is unchanged."""
    clip = Path(SUBSCRIBE_CLIP)
    if not clip.is_absolute():
        clip = BASE_DIR / clip
    if not clip.exists():
        log(f"subscribe animation: {clip.name} not found in brand/ — skipped")
        return v, a
    try:
        sw, sh = size
        anim_d = min(probe_duration(clip) or SUBSCRIBE_MAX_S, SUBSCRIBE_MAX_S, keep)
        st = max(0.0, keep - anim_d)
        key = _key_colour(clip)
        keyf = (f",colorkey={key}:0.30:0.12" if key else "")
        # A full-frame 9:16 animation (button in the middle of a black frame)
        # is cropped to its middle band first; otherwise scaling the whole frame
        # and placing its TOP at 60% pushed the button off the bottom edge.
        cw, ch = _dims(clip)
        full_frame = bool(cw and ch and ch > cw * 1.3)
        cropf = "crop=iw:ih*0.22:0:(ih-ih*0.22)/2," if full_frame else ""
        w = int(sw * ((0.95 if full_frame else 0.78) if sh > sw else 0.42))
        y = f"H*{0.60 if sh > sw else 0.55}-h/2"
        v2, a2 = work / f"v{k:02d}s.mp4", work / f"a{k:02d}s.wav"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(v), "-t", f"{anim_d:.3f}", "-i", str(clip),
              "-filter_complex",
              f"[1:v]{cropf}scale={w}:-2{keyf},format=yuva420p,setpts=PTS-STARTPTS+{st:.3f}/TB[s];"
              f"[0:v][s]overlay=(W-w)/2:{y}:eof_action=pass,format=yuv420p[o]",
              "-map", "[o]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-r", str(FPS), str(v2)])
        if has_audio(clip):
            ms = int(st * 1000)
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-t", f"{anim_d:.3f}", "-i", str(clip),
                  "-filter_complex",
                  f"[1:a]aresample=44100,volume={SUBSCRIBE_SFX},adelay={ms}|{ms}[s];"
                  f"[0:a][s]amix=inputs=2:duration=first:normalize=0[o]",
                  "-map", "[o]", "-ac", "2", "-t", f"{keep:.3f}", str(a2)])
        else:
            a2 = a
        log(f"clip {k}: subscribe animation over its last {anim_d:.1f}s (key {key or 'none'})")
        return v2, a2
    except Exception as e:  # noqa: BLE001 — never lose a reel over the animation
        log(f"subscribe animation skipped: {type(e).__name__}: {e}")
        return v, a


def _audio_bed():
    d = BASE_DIR / "brand" / "audio"
    if not d.is_dir():
        return None
    beds = sorted(p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a"))
    return beds[0] if beds else None


# Flow clips come back 6-10s long (the 4/6/8/10 grid, rounded up) while the
# line in them takes 3-6s to say, so each clip carries dead air, mostly at the
# end. Joined whole, that is a 2-4s gap at every cut. Trim each clip (picture
# and sound together) to its speech plus a short breath.
TRIM_SILENCE = os.getenv("STITCH_TRIM_SILENCE", "1").strip() != "0"
SPEECH_DROP_DB = float(os.getenv("STITCH_SPEECH_DROP_DB", "9"))   # this far under the voice = dead air
LEAD_PAD, TAIL_PAD, MIN_KEEP = 0.05, 0.10, 1.5


def speech_bounds(path, dur):
    """(first speech, last speech) in seconds, or None if it cannot tell.

    The cut-off is relative to the clip's own voice: Flow puts ambience and sound
    effects under the line at -24..-36 dB while the narration sits around -15 dB,
    so a fixed threshold either misses the dead air or eats the words. Speech
    level = 90th percentile of 100 ms windows; anything more than SPEECH_DROP_DB
    below it counts as not-speech."""
    import math, array
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                        "-ar", "16000", "-f", "s16le", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    pcm = array.array("h"); pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    win = 1600                                            # 100 ms
    dbs = []
    for i in range(0, len(pcm) - win + 1, win):
        seg = pcm[i:i + win]
        rms = math.sqrt(sum(x * x for x in seg) / win)
        dbs.append(20 * math.log10(rms / 32768 + 1e-9))
    if len(dbs) < 5:
        return None
    level = sorted(dbs)[int(len(dbs) * 0.9)]
    if level < -45:
        return None                                       # nothing audible
    thr = level - SPEECH_DROP_DB
    loud = [i for i, v in enumerate(dbs) if v >= thr]
    if not loud:
        return None
    first, last = loud[0] * 0.1, (loud[-1] + 1) * 0.1
    if last - first < 0.3:
        return None
    return max(0.0, first), min(dur, last)


def keep_window(src, d):
    """(start, length) of the part of the clip to keep."""
    if not TRIM_SILENCE or not has_audio(src):
        return 0.0, d
    b = speech_bounds(src, d)
    if not b:
        return 0.0, d
    a = max(0.0, b[0] - LEAD_PAD)
    z = min(d, b[1] + TAIL_PAD)
    if z - a < MIN_KEEP:
        return 0.0, d
    return a, z - a


REEL_LUFS = float(os.getenv("REEL_LUFS", "-14"))
REEL_MAX_S = float(os.getenv("REEL_MAX_S", "179"))


def stitch(clips, reel_path, work_dir, size=(W, H)):
    sw, sh = size
    work = Path(work_dir) / f"_stitch_{sw}x{sh}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    overlay = _handle_overlay(work / "handle.png", size)

    vids, auds, total = [], [], 0.0
    for k, src in enumerate(clips, 1):
        is_cta = src.get('is_cta') if isinstance(src, dict) else False
        labels = src.get('labels') if isinstance(src, dict) else None
        locator = src.get('locator') if isinstance(src, dict) else None
        wants_sub = bool(src.get('subscribe')) if isinstance(src, dict) else False
        src = Path(src['out']) if isinstance(src, dict) else Path(src)
        clip_overlay = _label_overlay(work / f"ov{k:02d}.png", labels, size, overlay)
        if locator and not is_cta:
            clip_overlay = _with_locator(work / f"ovl{k:02d}.png", clip_overlay, locator, size)
        overlay_k = clip_overlay
        d = probe_duration(src)
        if d <= 0.2:
            raise AssetGenerationError(f"{src.name} is empty ({d:.2f}s)")
        ss, keep = keep_window(src, d)
        total += keep
        v, a = work / f"v{k:02d}.mp4", work / f"a{k:02d}.wav"
        base = f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},fps={FPS},setsar=1"
        logo_path = Path("brand/logo.png")
        if is_cta and logo_path.exists():
            if overlay_k:
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(overlay_k), "-i", str(logo_path),
                       "-filter_complex", f"{base}[b];[b][1:v]overlay=0:0[b2];[2:v]scale=500:-1[logo];[b2][logo]overlay=(W-w)/2:(H-h)*0.40,format=yuv420p[o]", "-map", "[o]"]
            else:
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(logo_path),
                       "-filter_complex", f"{base}[b];[1:v]scale=500:-1[logo];[b][logo]overlay=(W-w)/2:(H-h)*0.40,format=yuv420p[o]", "-map", "[o]"]
        else:
            if overlay_k:
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(overlay_k),
                       "-filter_complex", f"{base}[b];[b][1:v]overlay=0:0,format=yuv420p[o]", "-map", "[o]"]
            else:
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                       "-vf", base.replace("[0:v]", "") + ",format=yuv420p"]
        _run(cmd + ["-an", "-ss", f"{ss:.3f}", "-t", f"{keep:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-profile:v", "high", "-crf", "20", "-r", str(FPS), str(v)])
        if has_audio(src):
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn",
                  "-af", f"atrim=start={ss:.3f},asetpts=PTS-STARTPTS,apad,aresample=44100,"
                         f"afade=t=in:d=0.03,afade=t=out:st={max(0.0, keep - 0.06):.3f}:d=0.06",
                  "-ac", "2", "-t", f"{keep:.3f}", str(a)])
        else:
            log(f"{src.name} has no audio track — silence for {d:.1f}s")
            _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                  "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{keep:.3f}", str(a)])
        if wants_sub and not is_cta:
            v, a = _with_subscribe(v, a, keep, work, k, size)
        vids.append(v)
        auds.append(a)
        cut = f" -> kept {ss:.2f}-{ss + keep:.2f}s ({keep:.1f}s)" if keep < d - 0.05 else ""
        log(f"clip {k}: {src.name} {d:.1f}s{cut}")

    (work / "v.txt").write_text("".join(f"file '{p}'\n" for p in vids))
    (work / "a.txt").write_text("".join(f"file '{p}'\n" for p in auds))
    mute, nar = work / "_mute.mp4", work / "_nar.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(work / "v.txt"), "-c:v", "copy", str(mute)])
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(work / "a.txt"), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
          "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(nar)])

    bed = None if FLOW_MUSIC else _audio_bed()
    if FLOW_MUSIC:
        log("FLOW_MUSIC=1: music comes from Flow, local bed skipped")
    if bed:
        level = float(os.getenv("MUSIC_LEVEL_DB", "-24"))
        mixed = work / "_mixed.wav"
        fade_st = max(0.0, total - 2.0)
        try:
            _run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(bed),
                  "-i", str(nar), "-filter_complex",
                  f"[0:a]volume={level}dB,afade=t=in:st=0:d=1,afade=t=out:st={fade_st:.2f}:d=2,"
                  f"atrim=0:{total:.3f}[b];[1:a][b]amix=inputs=2:duration=first:normalize=0[o]",
                  "-map", "[o]", "-ar", "44100", "-ac", "2", str(mixed)])
            nar = mixed
            log(f"audio bed: {bed.name} at {level} dB")
        except AssetGenerationError as e:
            log(f"audio bed skipped: {e}")

    reel_path = Path(reel_path)
    tmp = reel_path.with_suffix(".partial.mp4")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mute), "-i", str(nar),
          "-map", "0:v", "-map", "1:a", "-c:v", "copy",
          # Final loudness, after any music bed: YouTube and Instagram play at
          # about -14 LUFS and turn quiet uploads up by nothing (29 Sep reel: -16.3).
          "-af", f"loudnorm=I={REEL_LUFS}:TP=-1.5:LRA=11",
          "-c:a", "aac", "-b:a", "192k",
          "-ar", "44100", "-ac", "2", "-movflags", "+faststart", "-shortest", str(tmp)])
    verify_reel(tmp, total, size)
    tmp.replace(reel_path)       # the reel only appears once it is whole and checked
    shutil.rmtree(work, ignore_errors=True)
    return reel_path


def verify_reel(path, expected_s, size=(W, H)):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                "-of", "json", str(path)])
    streams = json.loads(out).get("streams", [])
    kinds = {s.get("codec_type") for s in streams}
    if not {"video", "audio"} <= kinds:
        raise AssetGenerationError(f"reel is missing a stream: {sorted(kinds)}")
    v = next(s for s in streams if s.get("codec_type") == "video")
    if (v.get("width"), v.get("height")) != tuple(size):
        raise AssetGenerationError(f"reel is {v.get('width')}x{v.get('height')}, not {size[0]}x{size[1]}")
    got = probe_duration(path)
    # YouTube Shorts and Instagram Reels both stop at 3 minutes; a vertical reel
    # at or over it would upload as a normal video / be refused. Never ship one.
    if size[1] > size[0] and got > REEL_MAX_S:
        raise AssetGenerationError(
            f"reel is {got:.1f}s — over the {REEL_MAX_S:.0f}s Shorts/Reels limit; not publishing it")
    if abs(got - expected_s) > 1.5:
        raise AssetGenerationError(f"reel is {got:.1f}s, clips add up to {expected_s:.1f}s")
    log(f"verified {path.name}: {got:.1f}s {size[0]}x{size[1]} with audio")


# ------------------------------------------------------------------ entry

def render_reel(script_data, output_dir, slug, aspect="9:16"):
    """Flow -> download -> stitch. Returns the reel path or raises AssetGenerationError."""
    if NARRATION_LANG == "hi":
        english = [i for i, sg in enumerate(script_data.get("segments") or [])
                   if str(sg.get("text") or "").strip() and not re.search(r"[\u0900-\u097F]", sg["text"])]
        if english:
            raise AssetGenerationError(
                f"script narration is not Hindi (segments {english}) — it was written before "
                f"NARRATION_LANG=hi; regenerate it rather than voice English as Hindi")
    job_path, clips = build_job(script_data, output_dir, slug, aspect)
    missing = [c for c in clips if not Path(c["out"]).exists()]
    log(f"[{aspect}] {len(clips)} scene(s), {len(missing)} still to generate in Flow")
    if missing:
        rc = run_flow(job_path)
        still = [c["out"] for c in clips if not Path(c["out"]).exists()]
        if still:
            code = {2: "Chrome/login problem — run ./login_to_flow.sh",
                    3: "Google Flow is OUT OF CREDITS — top up or wait for the monthly reset"}.get(rc, f"flow_gen exit {rc}")
            raise AssetGenerationError(
                f"{len(still)}/{len(clips)} clip(s) not downloaded ({code}); "
                f"the {len(clips) - len(still)} on disk are kept for --resume")
    if os.getenv("VOICE_DRIFT_FIX", "1") != "0":
        _fix_voice_drift(clips, job_path, aspect)
    if os.getenv("VISUAL_CHECK", "1") != "0":
        _check_visuals(clips, job_path, script_data)
    if os.getenv("LOCATOR_MAP", "1") != "0":
        _attach_locator(clips, script_data, output_dir, slug, aspect)
    reel = reel_path_for(output_dir, slug, aspect)
    if os.getenv("FLOW_REEL_SUFFIX"):
        reel = str(reel).replace(".mp4", os.getenv("FLOW_REEL_SUFFIX") + ".mp4")
    return str(stitch(clips, reel, output_dir, SIZES[aspect]))


VOICE_MAX_REGEN = int(os.getenv("VOICE_MAX_REGEN", "3"))


def _fix_voice_drift(clips, job_path, aspect):
    """Regenerate, once, the clips whose narrator pitch is off the reel's median.

    Each regeneration costs Flow credits, so at most VOICE_MAX_REGEN clips a reel
    and only once each: the first take is kept as <nn>.drift1.mp4, and whichever
    take is closer to the median is the one used. Never fails the render.
    """
    try:
        from core.voice_check import outliers
    except Exception as e:  # noqa: BLE001
        log(f"voice check skipped: {e}")
        return
    outs = [Path(c["out"]) for c in clips]
    med, pitch, off = outliers(outs)
    if not med:
        log("voice check: could not measure the narrator — skipped")
        return
    log(f"[{aspect}] narrator pitch median {med:.0f} Hz: "
        + " ".join(f"{i + 1}:{v:.0f}" for i, v in pitch.items() if v))
    todo = []
    for i in off:
        first = outs[i].with_suffix(".drift1.mp4")
        if first.exists():
            continue                       # already regenerated once
        if len(todo) >= VOICE_MAX_REGEN:
            break
        outs[i].rename(first)
        todo.append(i)
        log(f"clip {i + 1}: {pitch[i]:.0f} Hz is {abs(pitch[i] / med - 1):.0%} off — regenerating once")
    if not todo:
        return
    run_flow(job_path)
    from core.voice_check import voice_pitch
    for i in todo:
        first = outs[i].with_suffix(".drift1.mp4")
        if not outs[i].exists():
            first.rename(outs[i])
            log(f"clip {i + 1}: regeneration did not arrive — keeping the first take")
            continue
        new = voice_pitch(outs[i])
        if new is None or abs(new / med - 1) >= abs(pitch[i] / med - 1):
            outs[i].rename(outs[i].with_suffix(".drift2.mp4"))
            first.rename(outs[i])
            log(f"clip {i + 1}: new take {new or 0:.0f} Hz is no closer — keeping the first take")
        else:
            log(f"clip {i + 1}: new take {new:.0f} Hz (was {pitch[i]:.0f}) — using it")


VISUAL_MAX_REGEN = int(os.getenv("VISUAL_MAX_REGEN", "4"))


def _check_visuals(clips, job_path, script_data):
    """Stop wrong-country or inaccurate symbols, garbled writing and pictures
    that contradict the line from reaching an upload.

    Each raw Flow clip is shown to a vision model with its line. A failing clip
    is regenerated once with the problem named in its prompt (first take kept as
    <nn>.visual1.mp4). If the new take still fails, or the check cannot run,
    the render stops: the clips stay on disk for --resume, nothing is published.
    """
    from core import visual_check
    try:
        from google import genai
        from google.genai import types
        from core import model_client
    except Exception as e:  # noqa: BLE001
        raise AssetGenerationError(f"visual check cannot run: {e}")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise AssetGenerationError("visual check needs GEMINI_API_KEY")
    client = genai.Client(api_key=key)
    topic = str(script_data.get("title") or script_data.get("topic") or "")

    def check(i):
        c = clips[i]
        try:
            probs = visual_check.check_clip(client, c["out"], c.get("line", ""), topic,
                                            model_client.call_model, types)
            # the subscribe/CTA clip is allowed its own writing
            return [p for p in probs if not (c.get("is_cta") and p["kind"] == "text")]
        except Exception as e:  # noqa: BLE001
            raise AssetGenerationError(f"visual check failed on clip {i + 1}: {type(e).__name__}: {e}")

    bad = {}
    for i, c in enumerate(clips):
        probs = check(i)
        if probs:
            bad[i] = probs
            log(f"clip {i + 1}: " + "; ".join(f"{p['kind']}: {p['what']}" for p in probs))
    log(f"visual check: {len(clips) - len(bad)}/{len(clips)} clip(s) clean")
    if not bad:
        return
    if len(bad) > VISUAL_MAX_REGEN:
        raise AssetGenerationError(f"visual check: {len(bad)} clips show wrong symbols/garbled "
                                   f"text (limit {VISUAL_MAX_REGEN} regenerations) — not publishing")
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    todo = []
    for i, probs in bad.items():
        out = Path(clips[i]["out"])
        first = out.with_suffix(".visual1.mp4")
        if first.exists():
            raise AssetGenerationError(f"visual check: clip {i + 1} failed again after one "
                                       f"regeneration — not publishing")
        out.rename(first)
        extra = " " + visual_check.avoid_line(probs)
        job["clips"][i]["prompt"] = job["clips"][i]["prompt"] + extra
        clips[i]["prompt"] = job["clips"][i]["prompt"]
        todo.append(i)
        log(f"clip {i + 1}: regenerating once")
    Path(job_path).write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    run_flow(job_path)
    for i in todo:
        if not Path(clips[i]["out"]).exists():
            raise AssetGenerationError(f"visual check: clip {i + 1} regeneration did not arrive "
                                       f"— not publishing (rerun with --resume)")
        probs = check(i)
        if probs:
            raise AssetGenerationError(f"visual check: clip {i + 1} still shows "
                                       + "; ".join(f"{p['kind']}: {p['what']}" for p in probs)
                                       + " — not publishing")
        log(f"clip {i + 1}: new take is clean")


def _attach_locator(clips, script_data, output_dir, slug, aspect):
    """Mark the clips right after the hook to carry a real locator map
    (core/locator_map.py, drawn from geo/*.geojson, never by a model)."""
    try:
        from core import locator_map
        spec = None
        spec_path = naming.path(output_dir, slug, "topic_spec")
        if os.path.exists(spec_path):
            with open(spec_path, encoding="utf-8") as f:
                spec = json.load(f)
        tag = "" if aspect == "9:16" else "_169"
        png = locator_map.render(script_data, os.path.join(output_dir, f"{slug}_locator{tag}.png"),
                                 frame=SIZES[aspect], topic_spec=spec)
        if not png:
            log("locator map: nothing in the script matches the map data — no inset")
            return
        chosen = locator_map.scenes_for(script_data.get("segments"))
        for i, c in enumerate(clips, 1):
            if i in chosen and not c.get("is_cta"):
                c["locator"] = png
        log(f"locator map on clip(s) {sorted(chosen)}: {locator_map.match(script_data, spec)}")
    except Exception as e:  # noqa: BLE001
        log(f"locator map skipped: {type(e).__name__}: {e}")


def reel_path_for(output_dir, slug, aspect="9:16"):
    """<slug>_reel.mp4 for 9:16 (unchanged), <slug>_reel_169.mp4 for 16:9."""
    p = naming.path(output_dir, slug, "reel")
    return p if aspect == "9:16" else p.replace("_reel.mp4", "_reel_169.mp4")


if __name__ == "__main__":
    # ./venv/bin/python core/flow_stitch.py <slug>   — render (or resume) one topic
    # FLOW_MUSIC=1 FLOW_REEL_SUFFIX=_music ./venv/bin/python core/flow_stitch.py <slug>
    #   — A/B test: same script, music from Flow, written beside the published reel
    if len(sys.argv) < 2:
        sys.exit("usage: core/flow_stitch.py <slug>")
    # add --169 to render the 16:9 version
    s = sys.argv[1]
    od = BASE_DIR / "outputs" / s
    with open(naming.path(od, s, "script"), encoding="utf-8") as f:
        print(render_reel(json.load(f), str(od), s, "16:9" if "--169" in sys.argv else "9:16"))


def pad_916_to_169(src_path, dst_path):
    """Converts a 9:16 reel to 16:9 by padding it with a blurred background of itself."""
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src_path),
        "-filter_complex",
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=40:20,setsar=1[bg];"
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,setsar=1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-crf", "20",
        "-c:a", "copy", str(dst_path)
    ]
    log(f"Padding 9:16 to 16:9 -> {Path(dst_path).name}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        from pipeline_errors import AssetGenerationError
        raise AssetGenerationError(f"ffmpeg padding failed: {e}")
    return str(dst_path)

