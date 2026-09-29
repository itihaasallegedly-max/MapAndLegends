"""published.json — the permanent record of everything that went live.

Lives at the project root, OUTSIDE outputs/, so deleting outputs/ (or the
30-day cleanup) never loses what was published where. One entry per video
(slug), updated on every publish attempt that lands on a platform:

  {"<slug>": {"topic": ..., "title": ..., "source_url": ..., "first_live": ...,
              "platforms": {"youtube": {"id": "...", "url": "...", "live_at": ...},
                            "instagram": {"id": "...", "live_at": ...}, ...}}}

YouTube gets a watch URL from the id. Instagram's media id is not a public
link (the permalink needs an API call), so only the id is kept.
"""
import datetime
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(BASE_DIR, "published.json")


def load():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, LEDGER)


def _url(platform, vid):
    if not vid:
        return None
    if platform == "youtube":
        return f"https://youtube.com/shorts/{vid}"
    if platform == "youtube_169":
        return f"https://youtu.be/{vid}"
    return None


def _meta(slug):
    """Topic, title and source from the video's own records, when still on disk."""
    d = os.path.join(BASE_DIR, "outputs", slug)
    out = {}
    for suffix, keys in (("_topic_spec.json", ("topic", "url")), ("_script.json", ("title", "upload_title"))):
        try:
            with open(os.path.join(d, slug + suffix), encoding="utf-8") as f:
                j = json.load(f)
            for k in keys:
                if j.get(k):
                    out["source_url" if k == "url" else k] = j[k]
        except (OSError, json.JSONDecodeError):
            pass
    return out


def update(slug, platforms, when=None, extra=None):
    """Record every platform that is live for `slug`. Never removes anything."""
    live = {p: v for p, v in (platforms or {}).items() if isinstance(v, dict) and v.get("published")}
    if not live:
        return None
    data = load()
    e = data.setdefault(slug, {"platforms": {}})
    for k, v in {**_meta(slug), **(extra or {})}.items():
        e.setdefault(k, v)
    when = when or datetime.datetime.now().isoformat(timespec="seconds")
    for p, v in live.items():
        cur = e["platforms"].setdefault(p, {})
        cur["id"] = v.get("id") or cur.get("id")
        cur.setdefault("live_at", v.get("last_try") or when)
        if _url(p, cur["id"]):
            cur["url"] = _url(p, cur["id"])
    e.setdefault("first_live", min(v["live_at"] for v in e["platforms"].values()))
    _save(data)
    return e
