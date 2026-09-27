"""Continuity test: one narrator, one arc, existing Cyprus visuals. No Flow credits.

    ./venv/bin/python test_continuity.py              # voice hm_omega
    ./venv/bin/python test_continuity.py --voice hm_psi

Writes outputs/cyprus_continuity_test/cyprus_single_voice.mp4

What it tests, against the published reel (bGUt6JZ2Qzg):
  * the script has an arc: hook -> history -> cause -> each of the FOUR pieces
    named in turn ("pehla / doosra / teesra tukda") -> payoff that returns to
    the hook. Every line leads into the next.
  * one voice reads the whole script, so pitch, pace and tone stay the same.
  * each muted Flow clip is cut (or slowed) to fit its own line, so the picture
    follows the voice instead of the voice waiting for the picture.
"""
import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
TEST = BASE / "outputs" / "cyprus_continuity_test"
SRC = TEST / "src"
W, H, FPS, SR = 1080, 1920, 30, 24000

# (clip, line). Clips are the published reel's own scenes, audio stripped:
# v1 EU flag + island, v3 island split, v4 region map, v5 two communities,
# v6 British bases, v7 north in red / Greek flag, v8 UN green line.
LINES = [
    ("v3", "यूरोपीय संघ का एक देश, जिसका नक्शा चार टुकड़ों में बंटा है। ये है साइप्रस। पर ऐसा हुआ कैसे?"),
    ("v4", "सदियों तक यह द्वीप पहले यूनानी दुनिया का हिस्सा रहा, और फिर ऑटोमन साम्राज्य का।"),
    ("v5", "इसीलिए यहाँ दो समुदाय बसे, ग्रीक साइप्रियट और तुर्की साइप्रियट।"),
    ("v6", "फिर अंग्रेज़ आए। उन्नीस सौ साठ में उन्होंने आज़ादी दी, पर दो सैन्य अड्डे अपने पास रखे। यही है पहला टुकड़ा।"),
    ("v7", "आज़ादी के बाद दोनों समुदायों में तनाव बढ़ता गया। उन्नीस सौ चौहत्तर में ग्रीस के समर्थन से तख्तापलट हुआ, "
           "तो तुर्की ने सेना भेजकर उत्तर पर कब्ज़ा कर लिया। यह है दूसरा टुकड़ा।"),
    ("v8", "दोनों के बीच संयुक्त राष्ट्र ने बफर ज़ोन बनाया, जिसे ग्रीन लाइन कहते हैं। यह तीसरा टुकड़ा है।"),
    ("v1", "और बाकी दक्षिण है साइप्रस गणराज्य, जो यूरोपीय संघ का सदस्य है। "
           "यानी एक द्वीप, चार टुकड़े, और पचास साल से अधूरा बँटवारा।"),
]
GAP = 0.35          # breath between lines
GAP_QUESTION = 0.6  # a question gets a beat before the answer
MAX_SLOW = 1.35     # slow a short clip at most this much, then hold its last frame


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{cmd[0]} failed: {r.stderr[-800:]}")
    return r.stdout


def duration(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", str(p)]).strip())


def synth(lines, voice, speed, work):
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="h", repo_id="hexgrad/Kokoro-82M")
    out = []
    for i, (_, text) in enumerate(lines, 1):
        chunks = [a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a)
                  for _, _, a in pipe(text, voice=voice, speed=speed)]
        audio = np.concatenate(chunks)
        gap = GAP_QUESTION if text.rstrip().endswith("?") else GAP
        audio = np.concatenate([audio, np.zeros(int(gap * SR), dtype=audio.dtype)])
        p = work / f"line{i:02d}.wav"
        sf.write(str(p), audio, SR)
        out.append(p)
        print(f"[voice] line {i}: {len(audio) / SR:.2f}s")
    return out


def fit_clip(src, need, dst):
    have = duration(src)
    base = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    if have >= need:
        vf = base
    else:
        f = min(MAX_SLOW, need / have)
        hold = max(0.0, need - have * f)
        vf = f"setpts={f:.4f}*PTS,{base}" + (f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold else "")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an", "-vf", vf + f",fps={FPS},format=yuv420p",
         "-t", f"{need:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(dst)])
    return have


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="hm_omega", help="Kokoro Hindi voice: hm_omega, hm_psi, hf_alpha, hf_beta")
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()

    work = TEST / "_work"
    work.mkdir(parents=True, exist_ok=True)
    wavs = synth(LINES, a.voice, a.speed, work)

    vids = []
    t = 0.0
    for i, ((clip, text), wav) in enumerate(zip(LINES, wavs), 1):
        need = duration(wav)
        v = work / f"v{i:02d}.mp4"
        have = fit_clip(SRC / f"{clip}.mp4", need, v)
        how = "cut" if have >= need else f"slowed x{min(MAX_SLOW, need / have):.2f}"
        print(f"[scene] {i}: {t:5.1f}-{t + need:5.1f}s  {clip} ({have:.1f}s, {how})  {text[:40]}…")
        t += need
        vids.append(v)

    (work / "v.txt").write_text("".join(f"file '{p}'\n" for p in vids))
    (work / "a.txt").write_text("".join(f"file '{p}'\n" for p in wavs))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "v.txt"),
         "-c", "copy", str(work / "mute.mp4")])
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "a.txt"),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "2", str(work / "vo.wav")])
    audio = work / "vo.wav"
    beds = sorted((BASE / "brand" / "audio").glob("*.*")) if (BASE / "brand" / "audio").is_dir() else []
    beds = [b for b in beds if b.suffix.lower() in (".mp3", ".wav", ".m4a")]
    if beds:
        run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(beds[0]), "-i", str(audio),
             "-filter_complex", f"[0:a]volume=-24dB,afade=t=in:d=1,afade=t=out:st={max(0, t - 2):.2f}:d=2,"
             f"atrim=0:{t:.3f}[b];[1:a][b]amix=inputs=2:duration=first:normalize=0[o]",
             "-map", "[o]", "-ar", "44100", "-ac", "2", str(work / "mix.wav")])
        audio = work / "mix.wav"
        print(f"[audio] bed {beds[0].name} at -24 dB")
    out = TEST / f"cyprus_single_voice_{a.voice}.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(work / "mute.mp4"), "-i", str(audio),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-shortest", str(out)])
    print(f"\nDONE {out}  ({duration(out):.1f}s, {len(LINES)} scenes, one voice: {a.voice})")


if __name__ == "__main__":
    main()
