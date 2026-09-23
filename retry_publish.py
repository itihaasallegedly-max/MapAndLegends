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
    slugs = publish_queue.due()
    if not slugs:
        print(f"[retry] nothing due — {publish_queue.summary()}")
        return 0
    from core.uploader import publish_video
    for slug in slugs:
        state = publish_queue.load(slug)
        reel = state.get("reel")
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


if __name__ == "__main__":
    sys.exit(main())
