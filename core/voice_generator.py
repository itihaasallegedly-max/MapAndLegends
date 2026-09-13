"""Kokoro-82M voiceover with the master_vo.sh mastering chain.

Two silent failures used to live here: an Edge-TTS voice name in .env was
swapped for am_michael without a word, and a failed Kokoro run fell through
to macOS `say` (leaving orphan .aiff files behind as the only evidence).
Both are now hard errors.
"""
import os
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_errors import AssetGenerationError, ConfigError  # noqa: E402

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FREE_TTS_NARRATE = os.getenv(
    "FREE_TTS_NARRATE", os.path.expanduser("~/Claude/free-tts/narrate.py")
)
PYTHON_BIN = os.path.join(BASE_DIR, "venv", "bin", "python")

KOKORO_VALID_VOICES = {
    "am_michael", "am_onyx", "am_fenrir", "am_adam", "am_echo", "am_eric", "am_liam",
    "bm_george", "bm_fable", "bm_lewis", "bm_daniel",
    "af_sarah", "af_sky", "af_bella", "af_nicole", "bf_emma", "bf_isabella",
}


def resolve_voice(requested=None):
    """Return a valid Kokoro voice name, or raise saying exactly what's wrong.

    Previously an invalid name (e.g. the Edge-TTS 'en-IN-PrabhatNeural' that
    was sitting in .env) was silently replaced, so every reel shipped with a
    different voice than configured.
    """
    name = (requested or os.getenv("VOICE_NAME") or "").strip()
    if not name:
        raise ConfigError(
            "VOICE_NAME is not set. Choose one of: " + ", ".join(sorted(KOKORO_VALID_VOICES))
        )
    if name not in KOKORO_VALID_VOICES:
        raise ConfigError(
            f"VOICE_NAME={name!r} is not a Kokoro voice. It looks like an Edge-TTS name.\n"
            f"Kokoro voices: {', '.join(sorted(KOKORO_VALID_VOICES))}\n"
            f"Set one in .env — the pipeline will no longer substitute one for you."
        )
    return name


def generate_voiceover(text, output_path, voice=None, speed=None, lufs=None):
    """Render a mastered voiceover. Raises AssetGenerationError on failure."""
    if not str(text).strip():
        raise AssetGenerationError("Refusing to synthesize an empty voiceover script")

    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    kokoro_voice = resolve_voice(voice)
    speed = speed if speed is not None else os.getenv("VOICE_SPEED", "0.95")
    lufs = lufs if lufs is not None else os.getenv("VOICE_LUFS", "-16")

    if not os.path.exists(FREE_TTS_NARRATE):
        raise ConfigError(
            f"Kokoro narrator not found at {FREE_TTS_NARRATE}.\n"
            f"Set FREE_TTS_NARRATE in .env to the path of your narrate.py."
        )

    print(
        f"[VoiceGenerator] Kokoro-82M voice={kokoro_voice} speed={speed} target={lufs} LUFS"
    )

    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            temp_script_path = f.name

        cmd = [
            PYTHON_BIN if os.path.exists(PYTHON_BIN) else sys.executable,
            FREE_TTS_NARRATE,
            temp_script_path,
            output_path,
            "--voice", kokoro_voice,
            "--speed", str(speed),
            "--lufs", str(lufs),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.remove(temp_script_path)

    if res.returncode != 0 or not os.path.exists(output_path):
        raise AssetGenerationError(
            f"Kokoro narration failed (exit {res.returncode}).\n"
            f"stderr: {res.stderr.strip()[:600]}\n"
            f"stdout: {res.stdout.strip()[:300]}"
        )

    size = os.path.getsize(output_path)
    if size < 8_000:
        raise AssetGenerationError(
            f"Voiceover at {output_path} is only {size} bytes — treating as a failed render"
        )

    print(f"[VoiceGenerator] Mastered VO -> {output_path} ({size / 1024:.0f} KB)")
    return output_path


if __name__ == "__main__":
    demo = (
        "The Godavari is known as the Dakshina Ganga. Rising at Trimbakeshwar in "
        "Maharashtra, it flows east across the Deccan to the Bay of Bengal."
    )
    generate_voiceover(demo, os.path.join(BASE_DIR, "outputs", "_voice_test", "vo.mp3"))
