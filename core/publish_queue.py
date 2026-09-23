"""Per-reel publish state, so a failed upload is retried instead of lost.

State lives next to the reel: outputs/<slug>/<slug>_publish.json

    {"reel": "...", "platforms": {"youtube": {"published": false, "id": null,
      "error": "...", "attempts": 1, "next_try": "2026-09-23T05:00:00"}, ...},
     "abandoned": false}

A platform that is already live is never posted again: only the ones still
pending are retried, on a 1h / 4h / 12h / 24h / 24h backoff, then abandoned
(preflight reports abandoned reels as blocking).
"""
import datetime
import json
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import naming  # noqa: E402

OUTPUTS = os.path.join(BASE_DIR, "outputs")
BACKOFF_H = (1, 4, 12, 24, 24)
ALL_PLATFORMS = ("youtube", "instagram")


def _now():
    return datetime.datetime.now()


def _path(slug):
    return naming.path(os.path.join(OUTPUTS, slug), slug, "publish")


def load(slug):
    try:
        with open(_path(slug), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(slug, state):
    p = _path(slug)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def pending_platforms(state):
    return [k for k, v in state.get("platforms", {}).items() if not v.get("published")]


def record(slug, reel, result):
    """Merge one publish attempt's result into the reel's state and schedule retries."""
    state = load(slug) or {"reel": reel, "platforms": {}, "abandoned": False}
    state["reel"] = reel
    for platform in ALL_PLATFORMS:
        r = result.get(platform)
        if not isinstance(r, dict):
            continue
        entry = state["platforms"].setdefault(platform, {"attempts": 0})
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry.update(published=r["published"], id=r.get("id") or entry.get("id"),
                     error=r.get("error"), last_try=_now().isoformat(timespec="seconds"))
        if r["published"]:
            entry.pop("next_try", None)
        elif entry["attempts"] > len(BACKOFF_H):
            state["abandoned"] = True
            entry.pop("next_try", None)
        else:
            wait = BACKOFF_H[entry["attempts"] - 1]
            entry["next_try"] = (_now() + datetime.timedelta(hours=wait)).isoformat(timespec="seconds")
    save(slug, state)
    if not pending_platforms(state) and not state.get("abandoned"):
        cleanup_published(slug)
    return state


KEEP_SUFFIXES = ("_publish.json", "_script.json", "_topic_spec.json")


def is_live_everywhere(slug):
    state = load(slug)
    return bool(state) and not state.get("abandoned") and not pending_platforms(state)


def cleanup_published(slug):
    """Once the reel is live on every platform, delete everything local for it:
    the reel, the Flow clips, the downloaded competitor video, stitch leftovers.
    Only three small JSON records stay (a few KB): the publish record (platform
    IDs, and what tells --resume/preflight this topic is finished), the script
    and the topic spec."""
    d = os.path.join(OUTPUTS, slug)
    if not os.path.isdir(d):
        return 0
    freed = 0
    for name in os.listdir(d):
        path = os.path.join(d, name)
        if os.path.isfile(path) and name.endswith(KEEP_SUFFIXES):
            continue
        try:
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    freed += sum(os.path.getsize(os.path.join(root, f)) for f in files)
                shutil.rmtree(path)
            else:
                freed += os.path.getsize(path)
                os.remove(path)
        except OSError as e:
            print(f"[cleanup] could not delete {name}: {e}")
    print(f"[cleanup] {slug}: live everywhere, deleted local media ({freed / 1e6:.1f} MB freed)")
    return freed


def due(now=None):
    """Slugs with at least one platform whose retry time has come."""
    now = now or _now()
    out = []
    if not os.path.isdir(OUTPUTS):
        return out
    for slug in sorted(os.listdir(OUTPUTS)):
        state = load(slug)
        if not state or state.get("abandoned"):
            continue
        for p in pending_platforms(state):
            nt = state["platforms"][p].get("next_try")
            if nt and datetime.datetime.fromisoformat(nt) <= now:
                out.append(slug)
                break
    return out


def summary():
    s = {"pending": 0, "abandoned": 0, "live": 0}
    if not os.path.isdir(OUTPUTS):
        return s
    for slug in os.listdir(OUTPUTS):
        state = load(slug)
        if not state:
            continue
        if state.get("abandoned"):
            s["abandoned"] += 1
        elif pending_platforms(state):
            s["pending"] += 1
        else:
            s["live"] += 1
    return s
