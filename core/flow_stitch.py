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
NEG = ("full-bleed edge-to-edge vertical frame, no border, no letterbox, no on-screen "
       "text, no captions, no subtitles, no watermark, no logos")


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


def build_prompt(seg, style="", index=1, total=1, seconds=None, music_style=None):
    """One Flow prompt. The narration block goes FIRST: text-to-video models
    weight the start of a prompt, and the narration is what has to carry across
    clips; the picture only has to carry within one."""
    seconds = int(seconds or clip_seconds(seg))
    text = spoken_line(seg)
    lang = "Hindi" if NARRATION_LANG == "hi" else "English"
    parts = ["Vertical 9:16.", f"{seconds}-second clip."]
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
    parts.append(NEG + ".")
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
    tail = ("SOUND: the narration is the main sound. "
            + (("Very quiet background: " + "; ".join(sound) + ", far below the voice. ") if sound else "")
            + (("SOUND EFFECTS, each one clearly heard at its moment, sitting just under the "
                "voice and never covering a word: " + "; ".join(fxs) + ". ") if fxs else "")
            + (f"Soft background music from 0 seconds to the end: {music_style or MUSIC_STYLE}; always much quieter "
               "than the narration, steady volume, no swell, no sudden start or stop. No other voices, "
               "no singing. " if FLOW_MUSIC else "No music, no other voices, no singing. ")
            + (("When the narrator finishes, only the soft music continues." if FLOW_MUSIC
                else "When the narrator finishes, the clip ends almost silent.") if text else ""))
    parts.append(tail if text else tail + " No voiceover.")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def build_job(script_data, output_dir, slug):
    segments = script_data.get("segments") or []
    if not segments:
        raise AssetGenerationError("script has no segments")
    style = os.getenv("VIDEO_STYLE", "").strip()
    flow_dir = Path(output_dir) / ("flow_music" if FLOW_MUSIC else "flow")
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
        clips.append({"prompt": build_prompt(seg, style, index=i, total=n, seconds=secs, music_style=music_style),
                      "seconds": secs,
                      "out": str(flow_dir / f"{i:02d}.mp4")})
    job_path = Path(naming.path(output_dir, slug, "flow_job"))
    job_path.write_text(json.dumps({"aspect": "9:16", "clips": clips},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return job_path, clips


def run_flow(job_path):
    """Run the Flow downloader in its own process (a hung browser can be killed)."""
    try:
        rc = subprocess.run([sys.executable, str(BASE_DIR / "core" / "flow_gen.py"), str(job_path)],
                            cwd=BASE_DIR, timeout=FLOW_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        log(f"flow_gen exceeded {FLOW_TIMEOUT}s — downloaded clips are kept for the next run")
        return 1
    return rc


# ------------------------------------------------------------------ stitch

def _handle_overlay(path):
    """A small channel-handle footer, or None if there is no handle / no Pillow."""
    handle = os.getenv("CHANNEL_HANDLE", "").strip()
    if not handle:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
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
    d.text(((W - tw) / 2, H - 120), handle, font=font, fill=(217, 169, 76, 235),
           stroke_width=3, stroke_fill=(10, 23, 40, 200))
    img.save(path)
    return path


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
LEAD_PAD, TAIL_PAD, MIN_KEEP = 0.12, 0.35, 1.5


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


def stitch(clip_paths, reel_path, work_dir):
    work = Path(work_dir) / "_stitch"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    overlay = _handle_overlay(work / "handle.png")

    vids, auds, total = [], [], 0.0
    for k, src in enumerate(clip_paths, 1):
        src = Path(src)
        d = probe_duration(src)
        if d <= 0.2:
            raise AssetGenerationError(f"{src.name} is empty ({d:.2f}s)")
        ss, keep = keep_window(src, d)
        total += keep
        v, a = work / f"v{k:02d}.mp4", work / f"a{k:02d}.wav"
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1"
        if overlay:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(overlay),
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
          "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-ar", "44100", "-ac", "2", "-movflags", "+faststart", "-shortest", str(tmp)])
    verify_reel(tmp, total)
    tmp.replace(reel_path)       # the reel only appears once it is whole and checked
    shutil.rmtree(work, ignore_errors=True)
    return reel_path


def verify_reel(path, expected_s):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                "-of", "json", str(path)])
    streams = json.loads(out).get("streams", [])
    kinds = {s.get("codec_type") for s in streams}
    if not {"video", "audio"} <= kinds:
        raise AssetGenerationError(f"reel is missing a stream: {sorted(kinds)}")
    v = next(s for s in streams if s.get("codec_type") == "video")
    if (v.get("width"), v.get("height")) != (W, H):
        raise AssetGenerationError(f"reel is {v.get('width')}x{v.get('height')}, not {W}x{H}")
    got = probe_duration(path)
    if abs(got - expected_s) > 1.5:
        raise AssetGenerationError(f"reel is {got:.1f}s, clips add up to {expected_s:.1f}s")
    log(f"verified {path.name}: {got:.1f}s {W}x{H} with audio")


# ------------------------------------------------------------------ entry

def render_reel(script_data, output_dir, slug):
    """Flow -> download -> stitch. Returns the reel path or raises AssetGenerationError."""
    if NARRATION_LANG == "hi":
        english = [i for i, sg in enumerate(script_data.get("segments") or [])
                   if str(sg.get("text") or "").strip() and not re.search(r"[\u0900-\u097F]", sg["text"])]
        if english:
            raise AssetGenerationError(
                f"script narration is not Hindi (segments {english}) — it was written before "
                f"NARRATION_LANG=hi; regenerate it rather than voice English as Hindi")
    job_path, clips = build_job(script_data, output_dir, slug)
    missing = [c for c in clips if not Path(c["out"]).exists()]
    log(f"{len(clips)} scene(s), {len(missing)} still to generate in Flow")
    if missing:
        rc = run_flow(job_path)
        still = [c["out"] for c in clips if not Path(c["out"]).exists()]
        if still:
            code = {2: "Chrome/login problem — run ./login_to_flow.sh",
                    3: "Google Flow is OUT OF CREDITS — top up or wait for the monthly reset"}.get(rc, f"flow_gen exit {rc}")
            raise AssetGenerationError(
                f"{len(still)}/{len(clips)} clip(s) not downloaded ({code}); "
                f"the {len(clips) - len(still)} on disk are kept for --resume")
    reel = naming.path(output_dir, slug, "reel")
    if os.getenv("FLOW_REEL_SUFFIX"):
        reel = str(reel).replace(".mp4", os.getenv("FLOW_REEL_SUFFIX") + ".mp4")
    return str(stitch([c["out"] for c in clips], reel, output_dir))


if __name__ == "__main__":
    # ./venv/bin/python core/flow_stitch.py <slug>   — render (or resume) one topic
    # FLOW_MUSIC=1 FLOW_REEL_SUFFIX=_music ./venv/bin/python core/flow_stitch.py <slug>
    #   — A/B test: same script, music from Flow, written beside the published reel
    if len(sys.argv) < 2:
        sys.exit("usage: core/flow_stitch.py <slug>")
    s = sys.argv[1]
    od = BASE_DIR / "outputs" / s
    with open(naming.path(od, s, "script"), encoding="utf-8") as f:
        print(render_reel(json.load(f), str(od), s))
