"""NARRATION_MODE=rewrite (29 Sep 2026): the competitor's video is a SOURCE OF
FACTS, never a script to translate.

Facts are not copyrightable; a creator's wording, order, jokes, hook and
structure are. So the transcript never reaches the scriptwriter:

  1. narration_map.extract()  transcribes their narration (as before)
  2. extract_facts()          boils it down to bare research notes
                              ("Kosi River | length | 720 km"): no sentences,
                              no hooks, no jokes, no opinions, no order
  3. verbatim_runs()          any note that still carries 6+ consecutive words
                              of their narration is rejected
  4. brief()                  the notes handed to Prompt 2, which writes its own
                              story (STORY_BRIEF) without seeing their video
  5. check_originality()      after the script is written, a second model pass
                              compares it with their transcript and names any
                              scene that is a translation or close paraphrase;
                              Prompt 2 retries with that reason
"""
import json
import random
import re

FACTS_PROMPT = """
Below is the transcript of a short geography video. Turn it into RESEARCH
NOTES: the bare, checkable facts it states, and nothing of how it states them.

Rules:
- One fact per note, in the form "<subject> | <attribute> | <value>",
  e.g. "Kosi River | length | 720 km", "Cyprus | divided since | 1974",
  "Lake Superior | area compared to | larger than Austria".
- Only facts: names, places, numbers, dates, causes, comparisons of size.
- Leave out everything that is the creator's own expression: the opening
  question or hook, jokes, rhetorical lines, opinions, adjectives like
  "incredible", transitions, the ending, calls to subscribe.
- Never copy a sentence or a phrase from the transcript. Notes are data.
- Merge duplicates. Shuffle order does not matter; do not preserve theirs.

Return JSON only: {"facts": ["<subject> | <attribute> | <value>", ...]}

TRANSCRIPT:
"""

ORIGINALITY_PROMPT = """
You are checking a Hindi video script for originality against the English
transcript of another creator's video on the same topic.

Sharing FACTS is fine (names, numbers, dates, causes). What is NOT fine:
- a scene that is a translation of one of their lines, or a close paraphrase
  of it (same sentence structure, same comparison, same joke, same rhetorical
  question, same turn of phrase), or
- the overall script following their sequence of beats line for line, or
- the same opening hook or the same closing line.

Return JSON only:
{"copied_scenes": [{"scene": <1-based scene number>, "their_line": "<the line it copies>",
                    "why": "<translation | close paraphrase | same hook | same joke | same ending>"}],
 "same_structure": <true if the whole script follows their order beat for beat, else false>}

THEIR TRANSCRIPT:
{transcript}

OUR SCRIPT (scene number: text):
{script}
"""


def _json(raw):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def _words(t):
    return re.findall(r"[a-z0-9]+", str(t).lower())


def transcript_text(nm):
    return " ".join(ln["text"] for ln in (nm or {}).get("lines", []))


def verbatim_runs(notes, transcript, n=6):
    """Notes sharing a run of n+ consecutive words with the transcript."""
    tw = _words(transcript)
    grams = {tuple(tw[i:i + n]) for i in range(len(tw) - n + 1)}
    bad = []
    for note in notes:
        w = _words(note)
        if any(tuple(w[i:i + n]) in grams for i in range(len(w) - n + 1)):
            bad.append(note)
    return bad


def _call(client, call_model, types, prompt, what, models):
    def do(m):
        cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                          max_output_tokens=8192) if types else None
        return client.models.generate_content(model=m, contents=[prompt], config=cfg)
    if call_model:
        resp, _ = call_model(do, models=list(models), what=what)
    else:
        resp = do(models[0])
    return _json(resp.text)


def extract_facts(client, nm, call_model=None, types=None,
                  models=("gemini-3.5-flash-lite",)):
    """Research notes from their narration. None when there is nothing usable."""
    transcript = transcript_text(nm)
    if not transcript.strip():
        return None
    notes = []
    for attempt in (1, 2):
        extra = ("" if attempt == 1 else
                 "\nYOUR LAST NOTES COPIED THEIR WORDING. Rewrite as subject | attribute | value data.\n")
        data = _call(client, call_model, types, FACTS_PROMPT + extra + transcript,
                     "Rewrite: fact notes", models)
        notes = [" ".join(str(f).split()) for f in (data.get("facts") or []) if str(f).strip()]
        bad = verbatim_runs(notes, transcript)
        if not bad:
            break
        print(f"[Rewrite] {len(bad)} note(s) carried their wording (attempt {attempt})")
    notes = [f for f in notes if f not in verbatim_runs(notes, transcript)]
    random.shuffle(notes)     # their order is part of their expression
    return notes or None


def brief(notes):
    if not notes:
        return ""
    body = "\n".join(f"    - {n}" for n in notes)
    return f"""
    RESEARCH NOTES (facts only, in no particular order):
{body}

    These are raw facts about the topic, not a script. Build YOUR OWN video
    from them: choose your own angle and hook, your own order and your own
    ending. You may leave out weak facts, and you may add well-known facts
    that are accurate and checkable. Every fact you state goes into "facts".
    Do not write in the style of any other channel.
"""


def check_originality(client, nm, segments, call_model=None, types=None,
                      models=("gemini-3.5-flash-lite",)):
    """None when the script reads as our own, else a retry reason."""
    transcript = "\n".join(f"- {ln['text']}" for ln in (nm or {}).get("lines", []))
    if not transcript.strip():
        return None
    script = "\n".join(f"{i}: {s.get('text', '')}" for i, s in enumerate(segments, 1))
    try:
        data = _call(client, call_model, types,
                     ORIGINALITY_PROMPT.replace("{transcript}", transcript).replace("{script}", script),
                     "Rewrite: originality check", models)
    except Exception as e:  # noqa: BLE001
        print(f"[Rewrite] originality check could not run: {type(e).__name__}: {e}")
        return "__unchecked__"
    copied = [c for c in (data.get("copied_scenes") or []) if isinstance(c, dict)]
    same = bool(data.get("same_structure"))
    if not copied and not same:
        return None
    # Their line is logged, never handed back: quoting it would put their
    # wording into the next prompt.
    for c in copied:
        print(f"[Rewrite] scene {c.get('scene')} too close ({c.get('why')}): {str(c.get('their_line', ''))[:90]}")
    parts = [f"scene {c.get('scene')} reads as a {c.get('why', 'close paraphrase')} of the source"
             for c in copied]
    if same:
        parts.append("the script follows their sequence beat for beat")
    return ("Too close to the source video: " + "; ".join(parts)
            + ". Say those facts in a completely different way, with your own angle, "
              "comparison and order. Keep the facts, change everything else.")
