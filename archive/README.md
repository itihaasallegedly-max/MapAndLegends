# Archived, not deleted

These were the first-generation pipeline. They are kept for reference only —
nothing in the live pipeline imports them.

| File | Why it was retired |
|---|---|
| `auto_pipeline.py` | Second orchestrator writing into the same `outputs/` folder as `pipeline_daily.py`, with a different script schema (`scenes` vs `segments`) and its own dedup state. Running both meant duplicate topics and 4 posts a day. |
| `auto_scheduler.py` | In-process `schedule` loop posting 3x daily. Overlapped with the cron job and died with the terminal session. |
| `core/script_generator.py` | The `scenes`-schema script generator, with its own silent template fallback. |
| `core/topic_selector.py` | Queue-based topic selection; superseded by the weighted draw plus the SHA1 ledger in `stage2_daily_draw.py`. |
| `content_queue.json` | 8 hand-written topics, all still marked `pending` after 3 were produced because `mark_topic_completed` was never reached. Now redundant against the 558-topic backlog. |
| `record_reel_content.py` | Headless Playwright scrape of the reference Instagram profile. It only ever captured the logged-out interstitial: nav labels, the footer, and the 50-language selector. `on_screen_texts` came back empty every time. |

If you want any of this back, move the file up one level and re-add the import.
