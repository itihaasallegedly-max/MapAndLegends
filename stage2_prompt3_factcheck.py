"""Prompt 3 — independent fact-check gate.

The old local verifier in this file marked every claim "verified" unless it
contained one of three magic strings, so the gate could not return false.
That fallback is gone. If the check cannot run, the run aborts; it never
counts as a pass.

The gate's verdict is also recomputed here from the individual verdicts
rather than trusting the model's own `safe_to_publish` boolean — the model
is asked to judge claims, not to decide policy.
"""
import json
import os
import re

from dotenv import load_dotenv
from google import genai

from pipeline_errors import ConfigError, FactCheckError

load_dotenv()

VALID_STATUSES = {"verified", "uncertain", "false"}
MAX_UNCERTAIN = 1


def _extract_json(raw):
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise FactCheckError(f"No JSON object in fact-check response: {raw[:300]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise FactCheckError(f"Fact-check returned malformed JSON ({e}): {text[start:start + 300]!r}") from e


def evaluate_verdicts(verdicts, claims):
    """Decide publishability from the verdicts. Pure function, unit-testable.

    Returns (safe_to_publish, reason). Anything unexpected is a refusal, not
    a pass: an unparseable status, a claim with no verdict, or a verdict for
    a claim that was never made.
    """
    if len(verdicts) != len(claims):
        return False, f"gate returned {len(verdicts)} verdicts for {len(claims)} claims"

    statuses = []
    for v in verdicts:
        status = str(v.get("status", "")).strip().lower()
        if status not in VALID_STATUSES:
            return False, f"unrecognised status {status!r} on claim {v.get('claim')!r}"
        statuses.append(status)

    false_count = statuses.count("false")
    uncertain_count = statuses.count("uncertain")

    if false_count:
        return False, f"{false_count} claim(s) judged false"
    if uncertain_count > MAX_UNCERTAIN:
        return False, f"{uncertain_count} claims uncertain (limit {MAX_UNCERTAIN})"
    if uncertain_count:
        return True, f"passed with {uncertain_count} uncertain claim"
    return True, "all claims verified"


def fact_check_claims(facts_array):
    """Run the gate in a fresh context. Raises FactCheckError if it cannot run."""
    if not facts_array:
        raise FactCheckError("No claims to check — Prompt 2 produced an empty facts array")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ConfigError("GEMINI_API_KEY missing in environment.")

    model = os.getenv("GEMINI_TEXT_MODEL")
    if not model:
        raise ConfigError("GEMINI_TEXT_MODEL missing in .env — run diagnose_api.py")

    client = genai.Client(api_key=api_key)
    prompt = f"""
    Below are factual claims from a geography script. Judge each one independently.
    You have no knowledge of why the script was written and should not try to be helpful
    to it — your only job is accuracy.

    <claims>
    {json.dumps(facts_array, indent=2, ensure_ascii=False)}
    </claims>

    Return JSON only, with exactly one verdict per claim, in the same order:

    {{
      "verdicts": [
        {{"claim": "<claim, copied verbatim>", "status": "verified|uncertain|false", "note": "<short reason>"}}
      ]
    }}

    Rules:
    - "verified" means you are confident the claim is true as stated.
    - "uncertain" means you cannot confirm it, including anything time-sensitive.
    - "false" means the claim is wrong as stated, including a wrong number or a
      superlative that is not an official designation.
    - A vague or self-referential claim that asserts nothing checkable is "false",
      not "verified".
    - Be strict. An error on exam-prep content is expensive.
    """

    try:
        response = client.models.generate_content(model=model, contents=prompt)
    except Exception as e:
        raise FactCheckError(
            f"Fact-check call failed for model {model!r}: {type(e).__name__}: {e}\n"
            f"The gate did not run. This is NOT a pass."
        ) from e

    if not getattr(response, "text", None):
        raise FactCheckError(f"Fact-check model {model!r} returned an empty response")

    result = _extract_json(response.text)
    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list):
        raise FactCheckError(f"Fact-check response has no verdicts list: {result!r}")

    safe, reason = evaluate_verdicts(verdicts, facts_array)
    out = {
        "verdicts": verdicts,
        "safe_to_publish": safe,
        "reason": reason,
        "gate_model": model,
        "claims_checked": len(facts_array),
    }
    print(f"[Prompt 3] Gate: {'PASS' if safe else 'HOLD'} — {reason}")
    return out


if __name__ == "__main__":
    demo = [
        "The Godavari originates at Trimbakeshwar in Nashik district, Maharashtra.",
        "The Godavari drains into the Bay of Bengal.",
    ]
    print(json.dumps(fact_check_claims(demo), indent=2, ensure_ascii=False))
