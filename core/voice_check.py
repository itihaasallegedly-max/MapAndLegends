"""Catch the clip where Flow's narrator comes out a different man.

Every Flow clip is voiced by a fresh generation. The prompt pins the voice
down word for word, but measured reels still had outliers: Penna (29 Sep)
ran 111-122 Hz with one clip at 138 Hz; the first rewrite reel had clips at
101 and 144 Hz around a 122 Hz median. A jump like that is heard as a second
narrator at the cut.

voice_pitch() measures the narrator's median pitch in a clip with numpy only
(autocorrelation over the loud, clearly voiced 40 ms frames, so the music bed
under the voice is ignored). Checked against librosa.pyin on both Penna reels:
within ~5 Hz on every clip.
"""
import subprocess

try:
    import numpy as np
except ImportError:          # the check is skipped, never fatal
    np = None

DRIFT = 0.12                 # more than 12% off the reel's median = a different-sounding voice


def _pcm(path, sr=16000):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout[:len(r.stdout) // 2 * 2], np.int16).astype(np.float32) / 32768


def voice_pitch(path, sr=16000):
    """Median narrator pitch in Hz, or None when it cannot tell."""
    if np is None:
        return None
    x = _pcm(path, sr)
    if len(x) < sr:
        return None
    win, hop = 640, 320
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    loud = frames[db >= np.percentile(db, 90) - 6]
    lo, hi = int(sr / 300), int(sr / 70)
    f0 = []
    for fr in loud:
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[win - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.45:
            f0.append(sr / k)
    return float(np.median(f0)) if len(f0) >= 20 else None


def outliers(paths, drift=DRIFT):
    """(median, {index: pitch}, [indexes off by more than `drift`, worst first])."""
    pitch = {i: voice_pitch(p) for i, p in enumerate(paths)}
    known = sorted(v for v in pitch.values() if v)
    if len(known) < 3:
        return None, pitch, []
    med = known[len(known) // 2]
    off = [i for i, v in pitch.items() if v and abs(v / med - 1) > drift]
    off.sort(key=lambda i: -abs(pitch[i] / med - 1))
    return med, pitch, off
