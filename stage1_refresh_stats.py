#!/usr/bin/env python
"""Stage 1 (weekly): refresh performance data and reweight the backlog.

This was effectively a no-op: it re-derived weights from the same hardcoded
seed numbers every week, so nothing ever changed. It now reports whether the
refresh actually observed anything, and exits non-zero when it did not — so
a silent weekly no-op shows up in the cron log instead of looking like work.
"""
import json
import sys

from dotenv import load_dotenv

from stage0_prompt1_backlog import generate_backlog
from stage0_seed import fetch_seed_data, load_seed_posts

load_dotenv()


def refresh_stats_and_reweight():
    print("=" * 58)
    print("STAGE 1 (WEEKLY): REFRESH STATS & REWEIGHT BACKLOG")
    print("=" * 58)

    # Read back what the channel's own published reels actually did before
    # rebuilding anything — this is the loop that makes a weekly refresh mean
    # something without paying for a scraper.
    from core import performance
    performance.refresh()
    state = performance.summary()
    if state["published"]:
        print(f"[Stage 1] own channel: {state['measured']} of {state['published']} "
              f"published reels measured"
              + ("" if state["ready"]
                 else f"; {state['needed']} more before they drive the weights"))

    seed_file = fetch_seed_data()
    posts, source = load_seed_posts(seed_file)
    print(f"[Stage 1] {len(posts)} posts, data_source={source}")

    topics_file = generate_backlog()
    with open(topics_file, "r", encoding="utf-8") as f:
        topics = json.load(f)

    print("\n[Stage 1] series weights:")
    for s in topics["series"]:
        print(
            f"  {s['name']:<20} median={s['observed_median_views']:>9,}  "
            f"n={s['observed_n']:<3} weight={s['weight']}"
        )

    shape = topics.get("shape_basis") or {}
    if shape.get("set_lift"):
        print(f"\n[Stage 1] set-shaped posts out-perform single-subject ones "
              f"{shape['set_lift']}x "
              f"({shape['set_median_views']:,} vs {shape['single_median_views']:,} "
              f"median views) — {topics.get('set_topic_count', 0)} set topics in "
              f"the backlog")

    observed = source in ("own_channel", "apify_scrape")
    print("\n" + "=" * 58)
    if source == "own_channel":
        print(f"STAGE 1 COMPLETE — {topics['total_backlog_count']} topics reweighted "
              f"from your own published reels")
    elif observed:
        print(f"STAGE 1 COMPLETE — {topics['total_backlog_count']} topics reweighted "
              f"from a fresh scrape")
    elif source == "teardown_observed":
        print("STAGE 1 RAN BUT LEARNED NOTHING NEW")
        print("  The weights come from the 13 Sep 2026 teardown — real numbers,")
        print("  but somebody else's channel and a fixed snapshot, so this week's")
        print("  weights match last week's. They start moving on their own once")
        print(f"  {performance.MIN_ROWS_TO_WEIGH} of your own reels have published and been measured.")
    else:
        print("STAGE 1 RAN BUT OBSERVED NOTHING")
        print("  seed.json holds hand-seeded priors, whose view counts are")
        print("  invented. Set APIFY_API_TOKEN in .env for the refresh to mean")
        print("  anything.")
    print("=" * 58)
    return topics_file, observed


if __name__ == "__main__":
    _, observed = refresh_stats_and_reweight()
    sys.exit(0 if observed else 1)
