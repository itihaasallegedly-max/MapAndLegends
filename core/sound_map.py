"""The competitor's sound design, heard and carried over event by event.

Prompt 2 used to hear the competitor video only as a script source: it wrote
its own "sfx" (one or two, "leave it empty rather than invent") and never
listed what THEY put under the voice. A firecracker crackle, a crowd cheer, a
map "pop" on a reveal: exactly the small details the channel is known for.

  1. extract()      one focused listening pass on the uploaded video: music,
                    ambience and every spot effect, each with an id (E1, E2 ...),
                    the second it lands on and what is on screen at that moment
  2. brief()        the list, handed to Prompt 2 with the rule that every id is
                    carried into the scene that tells the same moment ("ref")
  3. missing()      which ids the script dropped (the retry reason)
  4. fill_missing() anything still dropped on the last try is placed by time,
                    proportionally, so no event is lost to a stubborn model

A failed listening pass returns None and the run goes on exactly as before:
the sound map can never cost a reel.
"""
import json
import re
import subprocess

PROMPT = """
Listen to the attached video's AUDIO, start to end, and write down its sound
design. Ignore the words the narrator says; list everything else you hear.

- "music": is there music? its style, instruments, mood and tempo, and where it
  changes (enters, drops out, hits, swells). Describe it; never name a track,
  artist or library.
- "events": EVERY non-speech sound, in time order, including quiet ones under
  the voice: spot effects (firecrackers, explosions, whoosh, swoosh, pop, click,
  ding, cash register, camera shutter, typewriter, sword, gunshot, bell, drum
  hit, riser, impact, glitch...), crowd or voices (cheering, gasps, laughter,
  chanting), nature (thunder, wind, waves, birds, rain), ambience beds
  (marketplace murmur, city traffic, battlefield). For each: the second it
  starts, how long it lasts, how loud it sits against the voice, and what is on
  screen at that moment.
A missed sound is the failure this list exists to prevent. Do not invent a
sound you do not hear.

Return JSON only:
{
  "duration_s": <length of the video in seconds>,
  "music": {"present": true, "style": "<instruments, mood, tempo>",
            "changes": [{"at": 0.0, "what": "<enters / drops out / hit / swell>"}]},
  "events": [
    {"at": 0.0, "dur": 1.0, "kind": "sfx | ambience | crowd | nature",
     "sound": "<what it is, specifically: 'string of firecrackers crackling'>",
     "loudness": "loud | clear | subtle",
     "on_screen": "<what the picture shows at that moment>"}
  ]
}
"""


def _probe(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
        return float(out.strip())
    except Exception:
        return 0.0


def _json(raw):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def extract(client, video_file_obj, video_path=None, call_model=None, types=None,
            models=("gemini-3.5-flash-lite",)):
    """One listening pass. Returns the sound map dict (ids assigned) or None."""
    if video_file_obj is None:
        return None
    try:
        def do(m):
            cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                              max_output_tokens=16384) if types else None
            return client.models.generate_content(model=m, contents=[video_file_obj, PROMPT],
                                                  config=cfg)
        if call_model:
            resp, _ = call_model(do, models=list(models), what="Prompt 2 sound map")
        else:
            resp = do(models[0])
        sm = _json(resp.text)
    except Exception as e:  # noqa: BLE001 — optional pass, never fatal
        print(f"[SoundMap] listening pass failed, continuing without it: {type(e).__name__}: {e}")
        return None
    return normalise(sm, _probe(video_path) if video_path else 0.0)


def normalise(sm, probed_s=0.0):
    if not isinstance(sm, dict):
        return None
    events = []
    for e in sm.get("events") or []:
        if not isinstance(e, dict) or not str(e.get("sound") or "").strip():
            continue
        try:
            at = max(0.0, float(e.get("at", 0)))
        except (TypeError, ValueError):
            at = 0.0
        events.append({"at": round(at, 1), "dur": e.get("dur"),
                       "kind": str(e.get("kind") or "sfx").strip(),
                       "sound": str(e["sound"]).strip(),
                       "loudness": str(e.get("loudness") or "clear").strip(),
                       "on_screen": str(e.get("on_screen") or "").strip()})
    events.sort(key=lambda e: e["at"])
    for i, e in enumerate(events, 1):
        e["id"] = f"E{i}"
    music = sm.get("music") if isinstance(sm.get("music"), dict) else {}
    dur = probed_s or float(sm.get("duration_s") or 0) or (events[-1]["at"] + 1 if events else 0)
    return {"duration_s": round(dur, 2), "music": music, "events": events}


def brief(sm):
    """The Prompt 2 block. Empty string when there is nothing to carry."""
    if not sm or not (sm.get("events") or (sm.get("music") or {}).get("present")):
        return ""
    lines = [f"    - {e['id']} at {e['at']}s ({e['loudness']}, {e['kind']}): {e['sound']}"
             + (f" — on screen: {e['on_screen']}" if e["on_screen"] else "") for e in sm["events"]]
    mus = sm.get("music") or {}
    mline = (f"Their music: {mus.get('style')}." if mus.get("present") and mus.get("style")
             else "Their video has no music.")
    return f"""
    THE COMPETITOR'S SOUND DESIGN — MATCH IT, EVENT BY EVENT.
    Their small sound details are part of why they are watched. {mline}
    Every sound below was heard in their video ({sm['duration_s']}s long):
{chr(10).join(lines) if lines else "    (no spot effects)"}
    For EVERY id above, put it in the "sfx" of the scene of yours that shows the
    same moment of the story, as {{"at": <second within YOUR scene>, "cue": "<the
    same sound, as specifically>", "ref": "<the id>"}}. An ambience event goes in
    that scene's "ambience" too. Keep the sound itself the same (firecrackers stay
    firecrackers); only the timing moves to fit your scene. Do not drop any id —
    a script that leaves one out is rejected.
    "audio.music" in each scene follows their music, described, never named.
"""


def _refs(segments):
    got = set()
    for s in segments:
        for fx in ((s.get("audio") or {}).get("sfx") or []):
            if isinstance(fx, dict):
                for r in re.findall(r"E\d+", str(fx.get("ref") or "")):
                    got.add(r)
    return got


def missing(segments, sm):
    if not sm:
        return []
    got = _refs(segments)
    return [e for e in sm.get("events") or [] if e["id"] not in got]


def missing_reason(miss):
    return ("the competitor's sound design has events your script drops: "
            + "; ".join(f"{e['id']} {e['sound']} at {e['at']}s (on screen: {e['on_screen'] or '?'})"
                        for e in miss[:12])
            + ". Put each in the sfx of the scene showing that moment, with its \"ref\".")


def fill_missing(segments, sm):
    """Place anything still dropped by time, proportionally. Returns the ids placed."""
    miss = missing(segments, sm)
    if not miss:
        return []
    ours = sum(float(s.get("seconds") or 0) for s in segments) or 1.0
    theirs = float(sm.get("duration_s") or 0) or ours
    placed = []
    for e in miss:
        t = min(e["at"] * ours / theirs, ours - 0.1)
        start = 0.0
        for s in segments:
            secs = float(s.get("seconds") or 0)
            if t < start + secs or s is segments[-1]:
                audio = s.setdefault("audio", {}) if isinstance(s.get("audio"), dict) else s.setdefault("audio", {})
                if not isinstance(audio, dict):
                    s["audio"] = audio = {}
                audio.setdefault("sfx", []).append(
                    {"at": round(max(0.0, min(t - start, secs - 0.5)), 1), "cue": e["sound"],
                     "ref": e["id"], "placed": "by_time"})
                placed.append(e["id"])
                break
            start += secs
    return placed
