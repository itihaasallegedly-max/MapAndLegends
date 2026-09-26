#!/usr/bin/env python
"""Retry uploads that failed earlier (called by run_daily.sh and every 2h by the scheduler).

Only platforms that are not live yet are retried, so a Reel already on
Instagram is never posted twice while YouTube catches up.
"""
import json
import os
import sys

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

import naming  # noqa: E402
from core import publish_queue  # noqa: E402


def main():
    force = "--force" in sys.argv[1:]  # retry every pending platform now
    slugs = publish_queue.due(force=force)
    if not slugs:
        print(f"[retry] nothing due — {publish_queue.summary()}")
        return 0
    from core.uploader import publish_video
    for slug in slugs:
        state = publish_queue.load(slug)
        reel = state.get("reels") or state.get("reel")
        todo = publish_queue.pending_platforms(state)
        out_dir = os.path.join(BASE_DIR, "outputs", slug)
        try:
            with open(naming.path(out_dir, slug, "script"), encoding="utf-8") as f:
                script_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[retry] {slug}: script unreadable ({e}) — skipping")
            continue
        print(f"[retry] {slug}: retrying {', '.join(todo)}")
        result = publish_video(reel, script_data, platforms=todo)
        state = publish_queue.record(slug, reel, result)
        print(f"[retry] {slug}: still pending {publish_queue.pending_platforms(state) or 'nothing'}")
    return 0


def run():
    rc = main()
    try:
        check_youtube_processing()
    except Exception as e:  # a failed check must never block retries
        print(f"[check] YouTube processing check skipped: {type(e).__name__}: {e}")
    return rc


# ------------------------------------------------------------ YouTube processing
# A Short can upload fine and then stall or fail in YouTube's own processing:
# it sits on the channel as a grey tile, or vanishes after a copyright check.
# The upload itself reports success either way, so this asks YouTube afterwards.
CHECK_DAYS = 7          # stop asking about a video after a week
STUCK_AFTER_H = 2       # still "processing" after this long = worth a look


def _published_youtube():
    """(slug, state) for every reel live on YouTube whose processing is not settled."""
    import datetime
    import glob
    cutoff = datetime.datetime.now() - datetime.timedelta(days=CHECK_DAYS)
    for p in glob.glob(os.path.join(BASE_DIR, "outputs", "*", "*_publish.json")):
        slug = os.path.basename(os.path.dirname(p))
        state = publish_queue.load(slug)
        yt = (state or {}).get("platforms", {}).get("youtube") or {}
        if not (yt.get("published") and yt.get("id")):
            continue
        if yt.get("processing") in ("succeeded", "failed", "rejected", "deleted"):
            continue
        try:
            when = datetime.datetime.fromisoformat(yt.get("last_try") or "")
        except ValueError:
            continue
        if when >= cutoff:
            yield slug, state, when


def classify(item):
    """YouTube's answer for one video -> (status, detail)."""
    if item is None:
        return "deleted", "YouTube no longer returns this video (deleted, or removed after a claim)"
    st = item.get("status") or {}
    pd = item.get("processingDetails") or {}
    up = st.get("uploadStatus")
    if up in ("failed", "rejected", "deleted"):
        why = st.get("failureReason") or st.get("rejectionReason") or ""
        return ("failed" if up == "failed" else up), f"uploadStatus={up} {why}".strip()
    ps = pd.get("processingStatus")
    if ps in ("failed", "terminated"):
        return "failed", f"processingStatus={ps} {pd.get('processingFailureReason') or ''}".strip()
    if up == "processed" or ps == "succeeded":
        return "succeeded", "processed"
    return "processing", f"uploadStatus={up} processingStatus={ps}"


def check_youtube_processing(youtube=None):
    import datetime
    pending = list(_published_youtube())
    if not pending:
        return 0
    if youtube is None:
        from googleapiclient.discovery import build
        from core.uploader import youtube_credentials
        youtube = build("youtube", "v3", credentials=youtube_credentials(), cache_discovery=False)
    ids = [s["platforms"]["youtube"]["id"] for _, s, _ in pending]
    items = {}
    for i in range(0, len(ids), 50):
        resp = youtube.videos().list(part="status,processingDetails", id=",".join(ids[i:i + 50])).execute()
        items.update({it["id"]: it for it in resp.get("items", [])})
    problems = 0
    for slug, state, when in pending:
        vid = state["platforms"]["youtube"]["id"]
        status, detail = classify(items.get(vid))
        age_h = (datetime.datetime.now() - when).total_seconds() / 3600
        state["platforms"]["youtube"]["processing"] = status
        publish_queue.save(slug, state)
        if status == "succeeded":
            print(f"[check] {vid} processed OK ({slug})")
        elif status == "processing" and age_h < STUCK_AFTER_H:
            print(f"[check] {vid} still processing, {age_h:.1f}h after upload — normal ({slug})")
        else:
            problems += 1
            label = "STUCK" if status == "processing" else status.upper()
            msg = (f"!! YouTube {label}: https://youtube.com/shorts/{vid} — {detail}, "
                   f"{age_h:.1f}h after upload. Open it in YouTube Studio. ({slug})")
            print(msg)
            with open(os.path.join(BASE_DIR, "logs", "runs.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"started": datetime.datetime.now().isoformat(timespec="seconds"),
                                    "outcome": "youtube_processing_problem", "slug": slug,
                                    "video": vid, "status": status, "detail": detail,
                                    "hours_since_upload": round(age_h, 1)}, ensure_ascii=False) + "\n")
    return problems


if __name__ == "__main__":
    sys.exit(run())
