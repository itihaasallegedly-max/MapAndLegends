"""Prompt 3 — fact-check what the viewer actually HEARS (restored 29 Sep 2026).

The gate was removed on 20 Sep (commit af44031, "Removed fact-checking gate
entirely"). The Penna rewrite on 29 Sep then said the river originates in Sri
Sathya Sai district — it rises in the Nandi Hills, Karnataka — and nothing
stopped it. This version differs from the old one in two ways:

  * It checks the spoken Hindi narration and the on-screen labels, scene by
    scene, not only the `facts` list the writer filled in about itself. A
    claim the writer never listed is still a claim the viewer hears.
  * It asks for Google Search grounding first and falls back to the model's
    own knowledge only if grounding is not available on this key.

Verdicts are judged here, not by the model's own "safe" flag: any "false"
fails, more than MAX_UNCERTAIN "uncertain" fails, and a check that cannot run
raises FactCheckError — it never counts as a pass.
"""
import json
import os
import re

from pipeline_errors import FactCheckError

VALID = {"verified", "uncertain", "false"}
MAX_UNCERTAIN = int(os.getenv("FACTCHECK_MAX_UNCERTAIN", "1"))

PROMPT = """
You are a strict fact-checker for a Hindi geography Short aimed at Indian
competitive-exam students. You do not care whether the video gets made; your
only job is accuracy.

Below is every scene: the Hindi line the narrator SAYS and the English labels
shown on screen. Extract EVERY factual claim a viewer would take away (places,
where something starts/ends/flows, directions, numbers, dates, rankings,
comparisons), translate each into one plain English sentence, and judge it.

- "verified": you are confident it is true AS STATED, including its scope and
  relations (a river's source is where it rises, not where it enters a state;
  "near X" vs "downstream of X"; basin-wide vs one region).
- "uncertain": you cannot confirm it, or it is time-sensitive.
- "false": wrong as stated — a wrong place, direction, number, superlative, or
  a distorted relation or scope.
- An approximate number ("about 600 km") is verified if sources agree within
  about 10%.
- Loose narration ("quenches the thirst of 1.5 crore people" for "the basin
  supports 1.5 crore people") is "false" if it changes the meaning, else
  "verified" with a note.

Return JSON only:
{"claims": [{"scene": <n>, "claim": "<English sentence>", "status": "verified|uncertain|false",
             "correct": "<the accurate version, when not verified>", "note": "<short reason/source>"}]}

SCENES:
{scenes}
"""


def _extract_json(raw):
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        raise FactCheckError(f"No JSON object in fact-check response: {raw[:200]!r}")
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise FactCheckError(f"Fact-check returned malformed JSON ({e})") from e


def evaluate(claims):
    """(safe, reason). Pure; anything unexpected is a refusal."""
    if not claims:
        return False, "the checker returned no claims"
    statuses = []
    for c in claims:
        s = str(c.get("status", "")).strip().lower()
        if s not in VALID:
            return False, f"unrecognised status {s!r} on {c.get('claim')!r}"
        statuses.append(s)
    f, u = statuses.count("false"), statuses.count("uncertain")
    if f:
        return False, f"{f} claim(s) judged false"
    if u > MAX_UNCERTAIN:
        return False, f"{u} claims uncertain (limit {MAX_UNCERTAIN})"
    return True, ("all claims verified" if not u else f"passed with {u} uncertain claim")


def correction(result):
    """The retry reason for Prompt 2: which scene says what, and what is true."""
    bad = [c for c in result["claims"] if str(c.get("status")).lower() != "verified"]
    return ("FACT-CHECK FAILED. Fix these and change nothing else: "
            + " ".join(f"Scene {c.get('scene')}: \"{c.get('claim')}\" is {c.get('status')}"
                       + (f"; correct: {c.get('correct')}" if c.get("correct") else "")
                       + "." for c in bad)
            + " If you cannot state a fact accurately, leave it out.")


def fact_check_script(client, segments, call_model, types, models=None):
    """Check the spoken narration + labels. Raises FactCheckError if it cannot run."""
    models = models or [m for m in (os.getenv("FACTCHECK_MODEL", "").strip(),
                                    "gemini-3.5-flash-lite") if m]
    scenes = "\n".join(f"{i}: SAYS \"{s.get('text', '')}\"  LABELS {s.get('labels') or []}"
                       for i, s in enumerate(segments, 1))
    prompt = PROMPT.replace("{scenes}", scenes)

    grounded = os.getenv("FACTCHECK_GROUNDING", "1") != "0"
    resp, how = None, None
    if grounded:
        try:
            tool = types.Tool(google_search=types.GoogleSearch())
            resp, model = call_model(
                lambda m: client.models.generate_content(
                    model=m, contents=[prompt],
                    config=types.GenerateContentConfig(tools=[tool], max_output_tokens=16384)),
                models=models, what="Prompt 3 fact-check (grounded)")
            how = f"{model} + Google Search"
        except Exception as e:  # noqa: BLE001
            print(f"[Prompt 3] grounding unavailable or quota exhausted ({type(e).__name__}: {str(e)[:120]}); "
                  f"checking from model knowledge")
            resp = None
    if resp is None:
        try:
            resp, model = call_model(
                lambda m: client.models.generate_content(
                    model=m, contents=[prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json",
                                                       max_output_tokens=16384)),
                models=models, what="Prompt 3 fact-check")
            how = model
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "QuotaExhaustedError":
                raise
            raise FactCheckError(f"The fact-check could not run ({type(e).__name__}: {e}). "
                                 f"This is NOT a pass.") from e
    claims = _extract_json(getattr(resp, "text", "")).get("claims")
    if not isinstance(claims, list):
        raise FactCheckError("Fact-check response has no claims list")
    safe, reason = evaluate(claims)
    for c in claims:
        if str(c.get("status")).lower() != "verified":
            print(f"[Prompt 3]   scene {c.get('scene')} {c.get('status')}: {c.get('claim')}"
                  + (f" -> {c.get('correct')}" if c.get("correct") else ""))
    print(f"[Prompt 3] {'PASS' if safe else 'FAIL'} — {reason} ({len(claims)} claims, {how})")
    return {"claims": claims, "safe_to_publish": safe, "reason": reason,
            "gate": how, "claims_checked": len(claims)}
