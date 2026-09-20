"""What your own reels actually did — the feedback loop, without a scraper.

Stage 1 was reweighting the backlog from a fixed snapshot: first invented
numbers, then the 13 Sep teardown. Both are somebody else's channel, and
neither changes when you publish. The obvious fix was an Apify scrape of the
competitor, which costs money.

This is the better and cheaper one. The pipeline already publishes to YouTube
and Instagram; both will tell you what happened, for free, through the same
Google/Meta credentials the upload already uses. Measuring your own channel
beats measuring theirs anyway — their audience is not yours, and a topic that
worked for them at 767K followers may not work for you.

  performance.json   one row per published video: topic, series, whether it
                     was a set, the platform ids, and the view counts each
                     time they were refreshed.

`as_seed_posts()` hands those rows to Stage 0 in exactly the shape seed.json
already uses, so every weight — the series medians and the set/single lift —
is recomputed from your own results with no other change. Until there are
enough rows to mean anything, Stage 0 keeps using the teardown and says so.

Nothing here costs anything: YouTube's videos.list and Instagram's media
fields are free reads on credentials the pipeline already holds.
"""
import datetime
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(BASE_DIR, "performance.json")

# Below this the medians are noise: one lucky reel would swing every weight in
# the backlog. Until then Stage 0 stays on the teardown snapshot.
MIN_ROWS_TO_WEIGH = int(os.getenv("PERFORMANCE_MIN_ROWS", "8"))

# A reel needs a few days before its view count means anything. Stage 0's own
# 7-day rule then excludes anything newer from the medians.
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/videos"
GRAPH_API = "https://graph.facebook.com/v21.0"


def load(path=None):
    path = path or LEDGER
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("videos", []) if isinstance(data, dict) else data


def save(rows, path=None):
    path = path or LEDGER
    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "count": len(rows),
        "videos": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def record(topic_spec, script_data, publish_result, path=None):
    """Add one published video to the ledger. Idempotent on the topic slug."""
    rows = load(path)
    topic = str(topic_spec.get("topic", "")).strip()
    if not topic:
        return rows

    def _id(platform):
        entry = (publish_result or {}).get(platform) or {}
        return entry.get("id") if entry.get("published") else None

    row = {
        "topic": topic,
        "subject": topic_spec.get("subject"),
        "series": topic_spec.get("series"),
        "kind": topic_spec.get("kind", "single"),
        "set_size": topic_spec.get("set_size"),
        "title": str(script_data.get("title", "")).strip(),
        "caption": str(script_data.get("caption", "")).strip(),
        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "youtube_id": _id("youtube"),
        "instagram_id": _id("instagram"),
        "views": None,
        "views_youtube": None,
        "views_instagram": None,
        "fetched_at": None,
    }
    rows = [r for r in rows if r.get("topic") != topic] + [row]
    save(rows, path)
    print(f"[Performance] recorded {topic!r} "
          f"(yt={row['youtube_id']}, ig={row['instagram_id']}); "
          f"{len(rows)} videos in the ledger")
    return rows


def _fetch_youtube(ids):
    """{video_id: views} from videos.list — a free read, 50 ids per call."""
    if not ids:
        return {}
    try:
        import requests
    except ImportError:
        return {}

    out = {}
    token = _youtube_access_token()
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not token and not key:
        return {}

    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        params = {"part": "statistics", "id": ",".join(chunk)}
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            params["key"] = key
        try:
            resp = requests.get(YOUTUBE_API, params=params, headers=headers, timeout=60)
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                views = item.get("statistics", {}).get("viewCount")
                if views is not None:
                    out[item["id"]] = int(views)
        except Exception as e:  # noqa: BLE001 — a stats read must never break Stage 1
            print(f"[Performance] YouTube stats unavailable: {type(e).__name__}: {e}")
            break
    return out


def _youtube_access_token():
    """A live access token from the upload credentials, or None.

    Reading view counts needs the youtube.readonly scope. A token authorised
    before that scope was added will not carry it — re-run authorise_youtube()
    once and both scopes come back together.
    """
    token_file = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
    if not os.path.isabs(token_file):
        token_file = os.path.join(BASE_DIR, token_file)
    if not os.path.exists(token_file):
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(token_file)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        return creds.token
    except Exception as e:  # noqa: BLE001
        print(f"[Performance] could not use {token_file}: {type(e).__name__}: {e}")
        return None


def _fetch_instagram(ids):
    """{media_id: plays} from the Graph API — free with the publishing token."""
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    if not ids or not token:
        return {}
    try:
        import requests
    except ImportError:
        return {}

    out = {}
    for media_id in ids:
        try:
            resp = requests.get(
                f"{GRAPH_API}/{media_id}/insights",
                params={"metric": "plays,reach", "access_token": token}, timeout=60,
            )
            if resp.status_code != 200:
                continue
            for metric in resp.json().get("data", []):
                if metric.get("name") == "plays":
                    values = metric.get("values") or [{}]
                    out[media_id] = int(values[0].get("value") or 0)
        except Exception as e:  # noqa: BLE001
            print(f"[Performance] Instagram insights unavailable: {type(e).__name__}: {e}")
            break
    return out


def refresh(path=None):
    """Update every row's view counts in place. Returns (rows, updated_count)."""
    rows = load(path)
    if not rows:
        print("[Performance] ledger is empty — nothing published through the "
              "pipeline yet, so there is nothing to learn from")
        return rows, 0

    yt = _fetch_youtube([r["youtube_id"] for r in rows if r.get("youtube_id")])
    ig = _fetch_instagram([r["instagram_id"] for r in rows if r.get("instagram_id")])
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    updated = 0
    for row in rows:
        y = yt.get(row.get("youtube_id"))
        i = ig.get(row.get("instagram_id"))
        if y is None and i is None:
            continue
        row["views_youtube"] = y if y is not None else row.get("views_youtube")
        row["views_instagram"] = i if i is not None else row.get("views_instagram")
        # One number for the weights. Summed rather than maxed: a topic that
        # does 200K on each platform is worth more than one that does 200K on
        # one, and the medians only ever compare rows to each other.
        row["views"] = sum(v for v in (row["views_youtube"], row["views_instagram"])
                           if v is not None)
        row["fetched_at"] = now
        updated += 1

    save(rows, path)
    print(f"[Performance] refreshed {updated} of {len(rows)} videos "
          f"({len(yt)} from YouTube, {len(ig)} from Instagram)")
    return rows, updated


def as_seed_posts(path=None, min_rows=None):
    """The ledger in seed.json's shape, or [] when there is not enough of it.

    Stage 0 reads these exactly like scraped posts, so the series medians and
    the set/single lift are recomputed from your own channel with no other
    change anywhere.
    """
    min_rows = MIN_ROWS_TO_WEIGH if min_rows is None else min_rows
    rows = [r for r in load(path) if r.get("views")]
    if len(rows) < min_rows:
        return []
    return [
        {
            "url": (f"https://youtube.com/shorts/{r['youtube_id']}"
                    if r.get("youtube_id") else ""),
            "caption": r.get("caption") or r.get("topic", ""),
            "videoViewCount": int(r["views"]),
            "timestamp": r.get("published_at", ""),
            "title": (r.get("title") or r.get("topic", ""))[:60].upper(),
        }
        for r in rows
    ]


def summary(path=None):
    """One line for preflight: how close the loop is to closing."""
    rows = load(path)
    measured = [r for r in rows if r.get("views")]
    return {
        "published": len(rows),
        "measured": len(measured),
        "needed": max(MIN_ROWS_TO_WEIGH - len(measured), 0),
        "ready": len(measured) >= MIN_ROWS_TO_WEIGH,
    }


if __name__ == "__main__":
    refresh()
    print(json.dumps(summary(), indent=2))
