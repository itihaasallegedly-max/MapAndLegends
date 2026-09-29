"""The competitor's narration, heard line by line and carried over faithfully.

Prompt 2 used to watch the competitor video and then WRITE ITS OWN story in
Hindi ("do not translate line by line", plus our own hook / linking-word /
payoff rules). The result covered the same topic but did not say what they
said: different order, different details, a different opening and ending.
The user's rule (27 Sep 2026): the narration must be the competitor's
narration, in Hindi.

  1. extract()   one listening pass that writes down EVERY spoken line,
                 verbatim, with its start/end second and what is on screen
                 (N1, N2 ...), plus any text burned into the frame
  2. brief()     the lines handed to Prompt 2 with the rule: translate each one
                 into natural spoken Hindi, same order, same facts, same names,
                 nothing added, nothing dropped; each scene lists the ids it
                 carries in "narration_refs"
  3. missing()   which ids no scene carries (the retry reason)
  4. order_ok()  refs must run N1, N2, N3 ... in order across the scenes

A failed listening pass returns None and Prompt 2 falls back to the old
free-writing mode, so the pass can never cost a reel.
"""
import json
import re

PROMPT = """
Listen to the attached video and write down its NARRATION, start to end,
exactly as spoken. This is a transcript, not a summary.

- Every spoken sentence, verbatim, in the language it is spoken in. Do not
  paraphrase, shorten, correct or skip anything, including the opening line,
  asides, jokes, questions to the viewer and the closing call to action.
- Split it into lines at sentence or clause boundaries (even mid-sentence if needed), STRICTLY ONE LINE PER 2-6
  seconds of speech, in time order.
- SETUPS AND PUNCHLINES: when the narrator trails off and a sound, music cue,
  voice clip or visual finishes the thought (e.g. "Europe's reaction was
  something like..." followed by shouting, a record scratch, a meme sound),
  that setup is its OWN line ending in "...", and the next sentence starts a
  new line. Never merge a setup into the sentence after it. Describe what
  answers it in "punchline" (the sound/visual that completes the joke).
- For each line: the second it starts and ends, and what is on screen while it
  is said (the place, map, building or text shown).
- Also list any text burned into the frame (titles, labels, place names) with
  the second it appears.
If there is no speech at all, return an empty "lines" list.

Return JSON only:
{
  "language": "<language of the narration, e.g. English>",
  "lines": [
    {"start": 0.0, "end": 3.2, "text": "<exactly what is said>",
     "on_screen": "<what the picture shows while it is said>",
     "punchline": "<only for a setup line: the sound/visual that completes it, else empty>"}
  ],
  "screen_text": [{"at": 0.0, "text": "<text shown in the frame>"}]
}
"""


def _json(raw):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def extract(client, video_file_obj, call_model=None, types=None,
            models=("gemini-3.5-flash-lite",)):
    """One transcription pass. Returns the narration map (ids assigned) or None."""
    if video_file_obj is None:
        return None
    try:
        def do(m):
            cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                              max_output_tokens=32768) if types else None
            return client.models.generate_content(model=m, contents=[video_file_obj, PROMPT],
                                                  config=cfg)
        if call_model:
            resp, _ = call_model(do, models=list(models), what="Prompt 2 narration map")
        else:
            resp = do(models[0])
        nm = normalise(_json(resp.text))
    except Exception as e:  # noqa: BLE001 — optional pass, never fatal
        print(f"[NarrationMap] transcription failed, falling back to free writing: "
              f"{type(e).__name__}: {e}")
        return None
    return nm


def normalise(nm):
    if not isinstance(nm, dict):
        return None
    lines = []
    for ln in nm.get("lines") or []:
        if not isinstance(ln, dict) or not str(ln.get("text") or "").strip():
            continue
        try:
            start = max(0.0, float(ln.get("start", 0)))
            end = max(start, float(ln.get("end", start)))
        except (TypeError, ValueError):
            start = end = 0.0
        lines.append({"start": round(start, 1), "end": round(end, 1),
                      "text": " ".join(str(ln["text"]).split()),
                      "on_screen": str(ln.get("on_screen") or "").strip(),
                      "punchline": str(ln.get("punchline") or "").strip()})
    if not lines:
        return None
    lines.sort(key=lambda x: x["start"])
    for i, ln in enumerate(lines, 1):
        ln["id"] = f"N{i}"
    screen = [s for s in (nm.get("screen_text") or [])
              if isinstance(s, dict) and str(s.get("text") or "").strip()]
    return {"language": str(nm.get("language") or "").strip() or "unknown",
            "lines": lines, "screen_text": screen,
            "words": sum(len(ln["text"].split()) for ln in lines)}


def brief(nm, lang="hi"):
    """The Prompt 2 block. Empty string when there is nothing to carry."""
    if not nm:
        return ""
    target = ("everyday spoken Hindi (Devanagari) — the way a popular Hindi YouTuber "
              "talks, not textbook/news Hindi") if lang == "hi" else "natural spoken English"
    rows = [f"    - {ln['id']} [{ln['start']}-{ln['end']}s] \"{ln['text']}\""
            + (f"  (on screen: {ln['on_screen']})" if ln["on_screen"] else "")
            + (f"  [PUNCHLINE after this line: {ln['punchline']}]" if ln.get("punchline") else "")
            for ln in nm["lines"]]
    return f"""
    THE COMPETITOR'S NARRATION — THIS IS YOUR SCRIPT. TRANSLATE IT, DO NOT REWRITE IT.
    Below is every line their narrator speaks ({nm['language']}), in order, with
    its timing. Your narration is THEIR narration in {target}:
{chr(10).join(rows)}
    - Translate EVERY line, in the SAME ORDER. Same facts, same names, same
      numbers, same details, same examples, same questions to the viewer, the
      same opening line and the same closing line / call to action.
    - Do not add anything they do not say. Do not drop anything they do say.
      Do not reorder, merge away or summarise. No new hook, no new ending.
    - Translate for meaning in the way a Hindi narrator would say it — natural
      and fluent, not word-for-word — but every piece of information stays.
    - Group consecutive lines into scenes so each scene's seconds (4, 6, 8 or
      10) roughly match how long those lines run in their video. Every scene
      lists the ids it carries in "narration_refs" (e.g. ["N3", "N4"]), in
      order; every id appears in exactly one scene.
    - A line marked [PUNCHLINE] is a joke setup. Keep it as its own short
      line ending in "..." (e.g. "और यूरोप का रिएक्शन कुछ ऐसा था..."), end its
      scene on it, and put the punchline sound/visual in that scene's audio and
      final seconds. Dropping a setup or merging it into the next sentence kills
      the joke — that is the beat viewers remember.
    - "hook" is your translation of N1 (and segment 1's text starts with it).
    - TTS PACING LIMIT: The final TTS voice cannot read fast. If their sentence is extremely long (e.g., long lists), DO NOT cram it all into one 8 or 10-second scene. Break it down into two separate short sentences across two scenes. No scene's text may exceed 3.0 words per second.
    - GRAMMAR: Every scene's text MUST be a completely independent, grammatically valid sentence. It MUST end cleanly with a full stop. Do not let a single sentence spill over across multiple scenes.
    - A script that drops, reorders or adds lines is rejected.
"""


def _refs(segments):
    out = []
    for s in segments:
        for r in s.get("narration_refs") or []:
            m = re.fullmatch(r"\s*(N\d+)\s*", str(r))
            if m:
                out.append(m.group(1))
    return out


def missing(segments, nm):
    if not nm:
        return []
    got = set(_refs(segments))
    return [ln for ln in nm["lines"] if ln["id"] not in got]


def order_problem(segments, nm):
    """None, or a sentence saying where the refs break the competitor's order."""
    if not nm:
        return None
    refs = [int(r[1:]) for r in _refs(segments)]
    dup = sorted({r for r in refs if refs.count(r) > 1})
    if dup:
        return f"lines {', '.join('N%d' % d for d in dup)} appear in more than one scene"
    back = [(a, b) for a, b in zip(refs, refs[1:]) if b < a]
    if back:
        a, b = back[0]
        return f"N{b} comes after N{a} — keep their order"
    extra = [r for r in refs if r > len(nm["lines"])]
    if extra:
        return f"refs {extra} do not exist (there are {len(nm['lines'])} lines)"
    return None


def reason(miss, problem=None):
    parts = []
    if miss:
        parts.append("your narration drops the competitor's lines "
                     + "; ".join(f"{ln['id']} \"{ln['text'][:80]}\"" for ln in miss[:12])
                     + " — translate each one into the scene where it belongs and list its id "
                       "in that scene's narration_refs")
    if problem:
        parts.append(problem)
    return ". ".join(parts) + "."
