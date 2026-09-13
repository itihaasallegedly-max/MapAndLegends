"""Prompt 2 — script generation.

There is no template fallback in this module any more. If the model cannot
be reached, or returns something that does not satisfy the contract below,
this raises. A run that cannot write a real script must not produce a video.
"""
import json
import os
import re

from dotenv import load_dotenv
from google import genai

from pipeline_errors import ConfigError, ScriptGenerationError

load_dotenv()

MIN_RUNTIME_S = 40
MAX_RUNTIME_S = 65
REQUIRED_KEYS = ("title", "hook", "segments", "caption", "facts")


def _extract_json(raw):
    """Pull the JSON object out of a model response.

    Handles bare JSON, fenced blocks, and leading prose. Raises rather than
    guessing if there is no object in there at all.
    """
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ScriptGenerationError(
            f"No JSON object in model response (first 300 chars): {raw[:300]!r}"
        )
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ScriptGenerationError(
            f"Model returned malformed JSON ({e}): {text[start:start + 300]!r}"
        ) from e


def validate_script(data, topic_spec):
    """Enforce the contract. Returns the script; raises on any violation."""
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ScriptGenerationError(f"Script missing required keys: {missing}")

    segments = data["segments"]
    if not isinstance(segments, list) or len(segments) < 3:
        raise ScriptGenerationError(
            f"Expected at least 3 segments, got {len(segments) if isinstance(segments, list) else type(segments).__name__}"
        )
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or not str(seg.get("text", "")).strip():
            raise ScriptGenerationError(f"Segment {i} has no spoken text: {seg!r}")
        try:
            seg["seconds"] = float(seg.get("seconds", 0))
        except (TypeError, ValueError):
            raise ScriptGenerationError(f"Segment {i} has a non-numeric 'seconds': {seg!r}")

    total = sum(s["seconds"] for s in segments)
    if not MIN_RUNTIME_S <= total <= MAX_RUNTIME_S:
        raise ScriptGenerationError(
            f"Planned runtime {total:.0f}s is outside {MIN_RUNTIME_S}-{MAX_RUNTIME_S}s "
            f"for '{topic_spec['topic']}'"
        )

    facts = data["facts"]
    if not isinstance(facts, list) or not facts:
        raise ScriptGenerationError("Script returned an empty 'facts' array — nothing to fact-check")
    data["facts"] = [str(f).strip() for f in facts if str(f).strip()]
    if not data["facts"]:
        raise ScriptGenerationError("Every entry in 'facts' was blank")

    if not str(data["title"]).strip():
        raise ScriptGenerationError("Script returned a blank title")

    return data


def generate_script_prompt2(topic_spec):
    """Prompt 2: script, hook, segments, caption and a discrete facts array."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ConfigError("GEMINI_API_KEY missing in environment.")

    model = os.getenv("GEMINI_TEXT_MODEL")
    if not model:
        raise ConfigError("GEMINI_TEXT_MODEL missing in .env — run diagnose_api.py")

    subject = topic_spec.get("subject") or topic_spec["topic"]

    client = genai.Client(api_key=api_key)
    prompt = f"""
    You write scripts for 45-60 second vertical geography shorts aimed at an Indian audience,
    including competitive-exam aspirants.

    Subject of this video: {subject}
    Angle / framing: {topic_spec['topic']}
    Series: {topic_spec['series']}

    Return JSON only with this exact shape:

    {{
      "title": "<max 4 words, all caps, fits on a cover image>",
      "title_regional": "<the same title in the locally relevant script (Devanagari, Telugu, Tamil, Bengali, Assamese...) — for a non-Indian subject, return the English title>",
      "hook": "<first 3 seconds of spoken script — a question or a surprising number>",
      "segments": [
        {{"text": "<spoken line>", "visual": "<what is on screen>", "seconds": 4}},
        {{"text": "<spoken line>", "visual": "<what is on screen>", "seconds": 12}},
        {{"text": "<spoken line>", "visual": "<what is on screen>", "seconds": 15}},
        {{"text": "<spoken line>", "visual": "<what is on screen>", "seconds": 14}}
      ],
      "caption": "<Instagram/YouTube caption, 2 sentences + 5 hashtags>",
      "facts": ["<every discrete factual claim made, one per string>"]
    }}

    Rules:
    - Write about {subject} as a subject. "{topic_spec['topic']}" is the framing, not a noun to
      put in a sentence — never write phrases like "{topic_spec['topic']} spans vital terrain".
    - Total runtime must sum to between 45 and 60 seconds across segments.
    - Every factual claim must also appear in the `facts` array, phrased so it can be checked
      on its own without the surrounding script. An unverifiable claim in the script that is
      missing from `facts` defeats the downstream gate.
    - Each fact must be specific and falsifiable — a name, a number, a designation. Do not
      write self-referential filler such as "X is an established geographical subject".
    - Do not state a fact you are not confident is current. Prefer stable facts (geography,
      official designations) over changeable ones (rankings, populations, records).
    - No "largest/longest/highest" claim unless it is an official designation.
    - Plain spoken Indian English. No jargon, no filler.
    """

    try:
        response = client.models.generate_content(model=model, contents=prompt)
    except Exception as e:
        raise ScriptGenerationError(
            f"Gemini text call failed for model {model!r}: {type(e).__name__}: {e}\n"
            f"Run ./venv/bin/python diagnose_api.py to confirm this model ID is served by your key."
        ) from e

    if not getattr(response, "text", None):
        raise ScriptGenerationError(
            f"Model {model!r} returned an empty response "
            f"(finish_reason may indicate a safety block): {response!r}"
        )

    data = validate_script(_extract_json(response.text), topic_spec)
    total = sum(s["seconds"] for s in data["segments"])
    print(
        f"[Prompt 2] Script for '{subject}': {len(data['segments'])} segments, "
        f"{total:.0f}s planned, {len(data['facts'])} checkable claims"
    )
    return data


if __name__ == "__main__":
    spec = {
        "topic": "Course and Origin of River Godavari",
        "subject": "River Godavari",
        "series": "Sacred Rivers",
        "title_en": "GODAVARI",
    }
    print(json.dumps(generate_script_prompt2(spec), indent=2, ensure_ascii=False))
