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

    observed = source == "apify_scrape"
    print("\n" + "=" * 58)
    if observed:
        print(f"STAGE 1 COMPLETE — {topics['total_backlog_count']} topics reweighted "
              f"from observed data")
    else:
        print("STAGE 1 RAN BUT OBSERVED NOTHING")
        print("  seed.json holds hand-seeded priors, so this week's weights are")
        print("  identical to last week's. Set APIFY_API_TOKEN in .env for the")
        print("  refresh to mean anything.")
    print("=" * 58)
    return topics_file, observed


if __name__ == "__main__":
    _, observed = refresh_stats_and_reweight()
    sys.exit(0 if observed else 1)
