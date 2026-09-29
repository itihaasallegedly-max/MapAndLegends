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
- Keep every relation exact. "downstream of Kadapa" must stay "downstream of
  Kadapa", never become "near Kadapa"; keep "upstream", "between", "after",
  "north of", "in <district>" exactly as stated. Never merge two places.
- Keep a number's scope: if it is for the whole basin, say basin; never
  narrow it to one state or region.
- For a river, ALWAYS include "<river> | source | <place, district, state>"
  and "<river> | mouth | <place>" whenever the transcript says where it
  starts and ends. Where it ENTERS a state is not its source.
- For a river, road, border or other route, list the places along it as
  "<route> | course step <n> | <place>" in their real order from start to end.
  That order is geography, not the creator's expression.
- Merge duplicates.

Return JSON only: {"facts": ["<subject> | <attribute> | <value>", ...]}

TRANSCRIPT:
"""

ORIGINALITY_PROMPT = """
You are checking a Hindi video script for originality against the English
transcript of another creator's video on the same topic.

Sharing FACTS is fine (names, numbers, dates, causes, superlatives). A line
that only states a fact in plain words — "the Congo is the world's deepest
river, over 220 m on average" — is NOT copying, even if their line states the
same fact: there is no other way to say a number. Flag only EXPRESSION:
What is NOT fine:
- a scene that translates one of their lines INCLUDING its distinctive
  wording beyond the bare fact (their comparison, joke, rhetorical question,
  image or turn of phrase — "literally created", "from one side to the
  other"), or
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
    notes, extra = [], ""
    for attempt in (1, 2, 3):
        data = _call(client, call_model, types, FACTS_PROMPT + extra + transcript,
                     "Rewrite: fact notes", models)
        notes = [" ".join(str(f).split()) for f in (data.get("facts") or []) if str(f).strip()]
        bad = verbatim_runs(notes, transcript)
        lost = missing_endpoints(notes, transcript)
        if not bad and not lost:
            break
        extra = "\n"
        if bad:
            print(f"[Rewrite] {len(bad)} note(s) carried their wording (attempt {attempt})")
            extra += "YOUR LAST NOTES COPIED THEIR WORDING. Rewrite as subject | attribute | value data.\n"
        if lost:
            print(f"[Rewrite] notes dropped the {' and '.join(lost)} (attempt {attempt})")
            extra += (f"YOUR LAST NOTES DROPPED THE {' AND '.join(x.upper() for x in lost)}. The transcript "
                      f"states it: add it as '<name> | {lost[0]} | <place>'.\n")
    else:
        if lost:
            raise ValueError(f"fact notes still missing the {' and '.join(lost)} after 3 tries")
    notes = [f for f in notes if f not in verbatim_runs(notes, transcript)]
    # Not shuffled: the first review (Penna, 29 Sep) showed a shuffled list
    # sends the river to the sea in scene 3 and back upstream in scene 9. The
    # course follows geography; the writer is still told to pick its own angle.
    return notes or None


# Only phrasing that is about where a river starts or ends. "begins in March"
# or "a coastline on the Bay of Bengal" must not demand a source or a mouth.
_SOURCE_WORDS = re.compile(r"\b(originat\w*|rises (at|in|from)|has its source|its source (is|at|in|lies))\b", re.I)
_MOUTH_WORDS = re.compile(
    r"\b((empt\w*|drains?|falls?|flows?|discharges?|ends? its journey|ending its journey|"
    r"merges?)\s+(in|into)\s+the\s+(bay|sea|ocean|arabian|gulf|rann)|its mouth|river mouth)\b", re.I)


def missing_endpoints(notes, transcript):
    """['source'] / ['mouth'] when the transcript states it and no note carries it.

    The Penna notes (29 Sep) went straight to 'course step 2', and the writer
    then made the river rise in the district where it merely enters a state.
    """
    lost = []
    low = [n.lower() for n in notes]
    if _SOURCE_WORDS.search(transcript) and not any(
            re.search(r"\|\s*(source|origin|originates?|rises?|course step 1)\b", n) for n in low):
        lost.append("source")
    if _MOUTH_WORDS.search(transcript) and not any(
            re.search(r"\|\s*(mouth|ends?|end point|endpoint|drains into|empties into|outlet)\b", n) for n in low):
        lost.append("mouth")
    return lost


def brief(notes):
    if not notes:
        return ""
    body = "\n".join(f"    - {n}" for n in notes)
    return f"""
    RESEARCH NOTES (facts only, in no particular order):
{body}

    These are raw facts about the topic, not a script. Build YOUR OWN video
    from them, as ONE story a viewer can follow:
    - SCENE 1 IS A HOOK, not a textbook opening. Lead with the most surprising
      fact, a contrast or a stake ("this dry river feeds 1.5 crore people") —
      never "X originates at Y" as the first line.
    - GEOGRAPHY RUNS ONE WAY. A river goes source -> mouth, a route start ->
      end. Once the story reaches the sea or the end, it never goes back
      upstream. Put basin-wide numbers before or after the journey, not in
      the middle of it.
    - Every linking word must be true: "यानी"/"इसीलिए" only when the line
      really follows from the one before.
    - Keep each fact's exact scope and relation (downstream of, in <district>,
      the whole basin). Never turn "downstream of X" into "near X".
    - The river's SOURCE is the "source" note, never the place where it
      enters a state. Never state a direction, turn or relation ("turns
      north") that is not in the notes or certainly true.
    - IF SCENE 1 ASKS OR PROMISES SOMETHING, THE VIDEO MUST DELIVER IT, by name.
      "Not every national symbol is official" means the video later names which
      ones are not (lotus, mango, banyan...). Never open on a promise you drop.
    - Pictures show each subject in its real setting: a river dolphin in a
      river, never in sea waves; the animal itself clearly visible.
    - THE LAST SCENE IS A PAYOFF: a comparison that makes a number feel real,
      or the one thing to remember. Never end on a bare statistic.
    - EVERY SCENE CARRIES ONE SPECIFIC DETAIL from the notes: a number, a date,
      a century, a count, a size, or a named place/person — never a line of
      general praise ("अनूठी भव्यता", "प्राचीन इतिहास के लिए जाना जाता है").
      Prefer the surprising details (a count, a record, a date of rebuilding).
    - Labels: every scene gets 1-3 labels in English letters — the names and
      numbers that scene's line SAYS OUT LOUD (the line may say them in Hindi).
    - Indian names in their standard Hindi spelling (as Hindi Wikipedia and
      government sources write them), e.g. पापाग्नि, मायलावरम, सोमशिला.
    - You may leave out weak facts and add well-known facts that are accurate
      and checkable. Every fact you state goes into "facts". Do not write in
      the style of any other channel.
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


REVIEW_PROMPT = """
You are a first-time viewer of a 60-90 second Hindi geography Short, and a
careful editor. Judge the script below ONLY on these points and report
concrete problems. Do not report style preferences.

1. hook   - scene 1 gives no reason to keep watching (a plain textbook
            statement such as "X originates at Y").
2. order  - the geography jumps: e.g. a river reaches the sea, then the story
            goes back upstream; or places are out of their real order.
3. link   - a linking word claims a connection that is not there ("यानी",
            "इसीलिए" when the line does not follow from the one before).
4. fact   - a fact is wrong, or its scope/relation is distorted (e.g. a dam
            that is downstream of a city described as near it; a basin-wide
            number given as one region's). Use your own knowledge.
5. cta    - the subscribe line promises something the video does not deliver
            after it (e.g. "more secrets" when only a list follows).
6. ending - the last scene ends on a bare number or fact with no payoff.
7. spelling - an Indian name is not in its standard Hindi spelling.
8. promise - scene 1 asks a question or promises something ("not all symbols
            are official") that the rest of the video never answers or shows.
            This is the worst failure: the viewer stayed for nothing.
Also list every on-screen label that its scene's line does NOT say out loud
(a name or number shown but not spoken in that scene). Labels are in English
letters and the narration is Hindi: a label IS spoken when the line says the
same name in Devanagari (Somnath = सोमनाथ, Dwarka = द्वारका) or the same
number in Hindi words (25 m = पच्चीस मीटर). Only list a label whose name or
number is absent from that scene's line.

Return JSON only:
{"problems": [{"scene": <n>, "kind": "<hook|order|link|fact|cta|ending|spelling|promise>",
               "fix": "<one sentence: what to change>"}],
 "unspoken_labels": [{"scene": <n>, "label": "<label>"}]}

SCRIPT (scene: spoken line  [labels]):
{script}

SUBSCRIBE LINE (inserted after scene {cta_after}): {cta}
"""


def viewer_review(client, data, call_model=None, types=None,
                  models=("gemini-3.5-flash-lite",)):
    """(retry reason or None, [(scene, label)] to drop). Never raises."""
    segs = data.get("segments") or []
    script = "\n".join(f"{i}: {s.get('text', '')}  {s.get('labels') or []}"
                       for i, s in enumerate(segs, 1))
    prompt = (REVIEW_PROMPT.replace("{script}", script)
              .replace("{cta}", str(data.get("midroll_cta") or "(none)"))
              .replace("{cta_after}", str(data.get("midroll_after") or "-")))
    try:
        out = _call(client, call_model, types, prompt, "Rewrite: viewer review", models)
    except Exception as e:  # noqa: BLE001 — a quality pass, not a gate
        print(f"[Review] could not run: {type(e).__name__}: {e}")
        return None, [], []
    probs = [p for p in (out.get("problems") or []) if isinstance(p, dict) and p.get("fix")]
    drop = [(int(u["scene"]), str(u["label"])) for u in (out.get("unspoken_labels") or [])
            if isinstance(u, dict) and str(u.get("scene", "")).isdigit() and u.get("label")]
    for p in probs:
        print(f"[Review] scene {p.get('scene')} {p.get('kind')}: {p.get('fix')}")
    if not probs:
        return None, drop, []
    return ("A viewer review found problems: "
            + " ".join(f"Scene {p.get('scene')} ({p.get('kind')}): {p.get('fix')}" for p in probs)
            + " Fix exactly these and keep everything else."), drop, probs


def drop_labels(segments, drop):
    """Remove labels the scene does not say. Returns how many were removed."""
    from core.translit import spoken
    n = 0
    for scene, label in drop:
        if 1 <= scene <= len(segments):
            labs = segments[scene - 1].get("labels") or []
            text = segments[scene - 1].get("text", "")
            # The reviewer compares English labels with Hindi speech and got it
            # wrong on 29 Sep (every Gujarat label dropped): a label the line
            # says in Devanagari is kept whatever the reviewer thinks.
            keep = [l for l in labs if l.strip().lower() != label.strip().lower() or spoken(l, text)]
            n += len(labs) - len(keep)
            segments[scene - 1]["labels"] = keep
    return n
