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
VOICE = os.getenv("FLOW_VOICE", "a warm, clear, confident Indian male documentary narrator"
                  if NARRATION_LANG == "hi" else "a clear, warm Indian English documentary narrator")
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
    """The scene's planned length, stretched if the narration will not fit."""
    planned = float(seg.get("seconds") or 8)
    need = estimate_audio_duration(spoken_line(seg)) + 0.8
    return round(min(FLOW_MAX_S, max(planned, need)), 1)


def build_prompt(seg, style=""):
    shot = str(seg.get("video_prompt") or "A cinematic geography shot.").strip()
    parts = ["Vertical 9:16.", shot if shot.endswith((".", "!", "?")) else shot + "."]
    beats = [str(b).strip() for b in (seg.get("per_second") or []) if str(b).strip()]
    if beats:
        parts.append("Beat by beat: " + " | ".join(f"{i}s: {b}" for i, b in enumerate(beats)))
    bg = seg.get("background") or {}
    if isinstance(bg, dict) and bg.get("description"):
        parts.append(f"Background: {bg['description']}")
    if style:
        parts.append(f"Visual style: {style}.")
    parts.append(NEG + ".")
    audio = seg.get("audio") or {}
    if isinstance(audio, dict):
        sound = [str(audio.get("ambience") or "").strip()]
        for fx in audio.get("sfx") or []:
            if isinstance(fx, dict) and fx.get("cue"):
                sound.append(f"{fx['cue']} at {fx.get('at', 0)}s")
        sound = [s for s in sound if s]
        if sound:
            parts.append("Sound: " + "; ".join(sound) + ".")
    # Music is laid once under the whole reel at stitch time; per-clip music
    # would restart at every cut.
    parts.append("No music.")
    text = spoken_line(seg)
    lang = "Hindi" if NARRATION_LANG == "hi" else "English"
    if text:
        direction = ""
        if isinstance(audio, dict) and audio.get("voice"):
            direction = f", {audio['voice']}"
        parts.append(f'Voiceover in {lang} by {VOICE}{direction}, clear pronunciation, '
                     f'speaking exactly: "{text}"')
    else:
        parts.append("No voiceover.")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def build_job(script_data, output_dir, slug):
    segments = script_data.get("segments") or []
    if not segments:
        raise AssetGenerationError("script has no segments")
    style = os.getenv("VIDEO_STYLE", "").strip()
    flow_dir = Path(output_dir) / "flow"
    flow_dir.mkdir(parents=True, exist_ok=True)
    clips = [{"prompt": build_prompt(seg, style),
              "seconds": clip_seconds(seg),
              "out": str(flow_dir / f"{i:02d}.mp4")}
             for i, seg in enumerate(segments, 1)]
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
        total += d
        v, a = work / f"v{k:02d}.mp4", work / f"a{k:02d}.wav"
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1"
        if overlay:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(overlay),
                   "-filter_complex", f"{base}[b];[b][1:v]overlay=0:0,format=yuv420p[o]", "-map", "[o]"]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                   "-vf", base.replace("[0:v]", "") + ",format=yuv420p"]
        _run(cmd + ["-an", "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-profile:v", "high", "-crf", "20", "-r", str(FPS), str(v)])
        if has_audio(src):
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn",
                  "-af", "apad,aresample=44100", "-ac", "2", "-t", f"{d:.3f}", str(a)])
        else:
            log(f"{src.name} has no audio track — silence for {d:.1f}s")
            _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                  "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{d:.3f}", str(a)])
        vids.append(v)
        auds.append(a)
        log(f"clip {k}: {src.name} {d:.1f}s")

    (work / "v.txt").write_text("".join(f"file '{p}'\n" for p in vids))
    (work / "a.txt").write_text("".join(f"file '{p}'\n" for p in auds))
    mute, nar = work / "_mute.mp4", work / "_nar.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(work / "v.txt"), "-c:v", "copy", str(mute)])
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(work / "a.txt"), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
          "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(nar)])

    bed = _audio_bed()
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
    return str(stitch([c["out"] for c in clips], reel, output_dir))


if __name__ == "__main__":
    # ./venv/bin/python core/flow_stitch.py <slug>   — render (or resume) one topic
    if len(sys.argv) < 2:
        sys.exit("usage: core/flow_stitch.py <slug>")
    s = sys.argv[1]
    od = BASE_DIR / "outputs" / s
    with open(naming.path(od, s, "script"), encoding="utf-8") as f:
        print(render_reel(json.load(f), str(od), s))
