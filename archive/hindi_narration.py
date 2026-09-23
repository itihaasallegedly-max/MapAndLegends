"""Hindi voiceover lines for a script, one per segment (seg["text_hi"]).

The script is still written, validated and fact-checked in English (the
prompt, the validator's word maths and the facts list all assume English).
This adds a spoken-Hindi version of every line, which is what Flow is told
to voice. One Gemini call per script; a line that would not fit Flow's 10 s
clip is sent back once with the reason, the same way Prompt 2 retries.
"""
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import model_client  # noqa: E402
from pipeline_errors import ConfigError, QuotaExhaustedError, ScriptGenerationError  # noqa: E402

MAX_WORDS = int(os.getenv("HINDI_MAX_WORDS", "21"))   # ~2.3 Hindi words/s -> fits a 10 s clip
MODEL = os.getenv("HINDI_TEXT_MODEL", "gemini-3.5-flash-lite")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def narration_language():
    return os.getenv("NARRATION_LANG", "hi").strip().lower()


def _prompt(lines, subject, correction=""):
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
    return f"""{correction}Translate these voiceover lines for a short geography video about "{subject}"
into natural SPOKEN Hindi, the way a warm Indian documentary narrator talks on YouTube.

Rules:
- Devanagari script only. Everyday Hindi (Hindustani), not heavy Sanskritised Hindi.
- Keep place names, rivers, states and countries as Indians say them in Hindi
  (e.g. "Odisha" -> "ओडिशा", "Brahmaputra" -> "ब्रह्मपुत्र").
- Write every number as Hindi words ("1,450 km" -> "चौदह सौ पचास किलोमीटर").
- Same meaning and the same facts. Add nothing, drop nothing.
- Each line at most {MAX_WORDS} words, so it can be spoken in under 9 seconds.
- Exactly {len(lines)} lines, in the same order. An empty English line stays empty.

Return ONLY JSON: {{"lines": ["...", "..."]}}

English lines:
{numbered}
"""


def _problems(out, src):
    if not isinstance(out, list) or len(out) != len(src):
        return [f"return exactly {len(src)} lines (you returned {len(out) if isinstance(out, list) else 'none'})"]
    errs = []
    for i, (hi, en) in enumerate(zip(out, src), 1):
        hi = str(hi).strip()
        if en.strip() and not DEVANAGARI.search(hi):
            errs.append(f"line {i} is not in Devanagari")
        n = len(hi.split())
        if n > MAX_WORDS:
            errs.append(f"line {i} has {n} words, the maximum is {MAX_WORDS} — shorten it")
    return errs


def add_hindi_narration(script_data, subject=None):
    """Fill seg["text_hi"] for every segment. Returns script_data (mutated)."""
    segs = script_data.get("segments") or []
    src = [str(s.get("text") or "").strip() for s in segs]
    if not any(src):
        return script_data
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ConfigError("GEMINI_API_KEY missing — needed for the Hindi voiceover lines")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    subject = subject or script_data.get("subject") or script_data.get("title") or "geography"

    correction, out = "", None
    for attempt in (1, 2):
        try:
            resp, _ = model_client.call_model(
                lambda m: client.models.generate_content(
                    model=m, contents=[_prompt(src, subject, correction)],
                    config=types.GenerateContentConfig(response_mime_type="application/json",
                                                       max_output_tokens=4096)),
                models=[MODEL], what="Hindi narration")
        except QuotaExhaustedError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ScriptGenerationError(f"Hindi narration call failed: {type(e).__name__}: {e}") from e
        text = (resp.text or "").strip()
        try:
            out = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("lines")
        except (ValueError, AttributeError):
            out = None
        errs = _problems(out, src)
        if not errs:
            break
        print(f"[Hindi] attempt {attempt} rejected: {'; '.join(errs)}")
        correction = "YOUR PREVIOUS ATTEMPT WAS REJECTED: " + "; ".join(errs) + "\n\n"
    else:
        raise ScriptGenerationError("Hindi narration failed validation twice: " + "; ".join(errs))

    for seg, hi in zip(segs, out):
        seg["text_hi"] = str(hi).strip()
    script_data["voiceover_text_hi"] = " ".join(s["text_hi"] for s in segs if s["text_hi"])
    script_data["narration_lang"] = "hi"
    print(f"[Hindi] {len(segs)} line(s), {sum(len(s['text_hi'].split()) for s in segs)} words")
    return script_data
