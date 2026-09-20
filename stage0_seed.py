import datetime
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# HAND-SEEDED PRIORS — not scraped data.
#
# These 35 entries were written by hand and the view counts are invented.
# Every series weight in topics.json derives from them, so the "observed
# median views" in that file are editorial guesses, not analytics. Set
# APIFY_API_TOKEN in .env to replace them with real numbers; until then
# seed.json records data_source="hand_seeded_priors" so nothing downstream
# can mistake these for measurements.
SAMPLE_SEED_POSTS = [
    {"url": "https://www.instagram.com/p/C1", "caption": "National Symbols of India. The Bengal Tiger in modern drip.", "videoViewCount": 1450000, "timestamp": "2025-04-10T10:00:00Z", "title": "NATIONAL SYMBOLS"},
    {"url": "https://www.instagram.com/p/C2", "caption": "Temples of Andhra Pradesh. Architecture and sacred heritage.", "videoViewCount": 920000, "timestamp": "2025-04-12T10:00:00Z", "title": "TEMPLES OF ANDHRA PRADESH"},
    {"url": "https://www.instagram.com/p/C3", "caption": "Mahanadi River facts and origin.", "videoViewCount": 1200000, "timestamp": "2025-04-15T10:00:00Z", "title": "महानदी"},
    {"url": "https://www.instagram.com/p/C4", "caption": "State Animals of Indian States.", "videoViewCount": 850000, "timestamp": "2025-04-18T10:00:00Z", "title": "STATE ANIMALS"},
    {"url": "https://www.instagram.com/p/C5", "caption": "Temples of Maharashtra and Kailasa Temple.", "videoViewCount": 1100000, "timestamp": "2025-04-20T10:00:00Z", "title": "TEMPLES OF MAHARASHTRA"},
    {"url": "https://www.instagram.com/p/C6", "caption": "Tapi river course and geography.", "videoViewCount": 780000, "timestamp": "2025-04-22T10:00:00Z", "title": "तापी"},
    {"url": "https://www.instagram.com/p/C7", "caption": "State Fish of Indian States.", "videoViewCount": 640000, "timestamp": "2025-04-25T10:00:00Z", "title": "STATE FISH"},
    {"url": "https://www.instagram.com/p/C8", "caption": "Zonal Councils of India explained.", "videoViewCount": 510000, "timestamp": "2025-04-28T10:00:00Z", "title": "ZONAL COUNCILS"},
    {"url": "https://www.instagram.com/p/C9", "caption": "Greece facts and history.", "videoViewCount": 430000, "timestamp": "2025-05-01T10:00:00Z", "title": "GREECE"},
    {"url": "https://www.instagram.com/p/C10", "caption": "Why mosquitoes exist and geography of species.", "videoViewCount": 1800000, "timestamp": "2025-05-04T10:00:00Z", "title": "MOSQUITOES"},
    {"url": "https://www.instagram.com/p/C11", "caption": "Japan: Land of the Rising Sun.", "videoViewCount": 980000, "timestamp": "2025-05-08T10:00:00Z", "title": "JAPAN"},
    {"url": "https://www.instagram.com/p/C12", "caption": "Argentina profile.", "videoViewCount": 620000, "timestamp": "2025-05-10T10:00:00Z", "title": "ARGENTINA"},
    {"url": "https://www.instagram.com/p/C13", "caption": "Spain geography and culture.", "videoViewCount": 710000, "timestamp": "2025-05-12T10:00:00Z", "title": "SPAIN"},
    {"url": "https://www.instagram.com/p/C14", "caption": "Indian Rivers network overview.", "videoViewCount": 1650000, "timestamp": "2025-05-15T10:00:00Z", "title": "INDIAN RIVERS"},
    {"url": "https://www.instagram.com/p/C15", "caption": "Gujarat state facts.", "videoViewCount": 890000, "timestamp": "2025-05-18T10:00:00Z", "title": "गुजरात"},
    {"url": "https://www.instagram.com/p/C16", "caption": "Population density of India.", "videoViewCount": 1350000, "timestamp": "2025-05-20T10:00:00Z", "title": "POPULATION"},
    {"url": "https://www.instagram.com/p/C17", "caption": "Islands types: continental vs tidal.", "videoViewCount": 940000, "timestamp": "2025-05-22T10:00:00Z", "title": "ISLANDS"},
    {"url": "https://www.instagram.com/p/C18", "caption": "Telangana state facts.", "videoViewCount": 1050000, "timestamp": "2025-05-25T10:00:00Z", "title": "తెలంగాణ"},
    {"url": "https://www.instagram.com/p/C19", "caption": "Karnataka state profile.", "videoViewCount": 870000, "timestamp": "2025-05-28T10:00:00Z", "title": "ಕರ್ನಾಟಕ"},
    {"url": "https://www.instagram.com/p/C20", "caption": "Uttar Pradesh state profile.", "videoViewCount": 1500000, "timestamp": "2025-06-01T10:00:00Z", "title": "उत्तर प्रदेश"},
    {"url": "https://www.instagram.com/p/C21", "caption": "Tamil Nadu Rivers overview.", "videoViewCount": 1120000, "timestamp": "2025-06-05T10:00:00Z", "title": "தமிழ்நாடு ஆறுகள்"},
    {"url": "https://www.instagram.com/p/C22", "caption": "Madhya Pradesh state facts.", "videoViewCount": 990000, "timestamp": "2025-06-08T10:00:00Z", "title": "मध्य प्रदेश"},
    {"url": "https://www.instagram.com/p/C23", "caption": "European dances history.", "videoViewCount": 540000, "timestamp": "2025-06-10T10:00:00Z", "title": "EUROPEAN DANCES"},
    {"url": "https://www.instagram.com/p/C24", "caption": "Indonesia geography.", "videoViewCount": 680000, "timestamp": "2025-06-12T10:00:00Z", "title": "INDONESIA"},
    {"url": "https://www.instagram.com/p/C25", "caption": "Germany facts.", "videoViewCount": 730000, "timestamp": "2025-06-15T10:00:00Z", "title": "GERMANY"},
    {"url": "https://www.instagram.com/p/C26", "caption": "Thiruvananthapuram history.", "videoViewCount": 810000, "timestamp": "2025-06-18T10:00:00Z", "title": "തിരുവനന്തപുരം"},
    {"url": "https://www.instagram.com/p/C27", "caption": "35 Districts of Assam.", "videoViewCount": 1250000, "timestamp": "2025-06-20T10:00:00Z", "title": "35 DISTRICTS ASSAM"},
    {"url": "https://www.instagram.com/p/C28", "caption": "India Post history.", "videoViewCount": 960000, "timestamp": "2025-06-22T10:00:00Z", "title": "INDIA POST"},
    {"url": "https://www.instagram.com/p/C29", "caption": "Telangana Temples heritage.", "videoViewCount": 1080000, "timestamp": "2025-06-25T10:00:00Z", "title": "TELANGANA TEMPLES"},
    {"url": "https://www.instagram.com/p/C30", "caption": "Kaveri River story.", "videoViewCount": 1400000, "timestamp": "2025-06-28T10:00:00Z", "title": "KAVERI"},
    {"url": "https://www.instagram.com/p/C31", "caption": "Krishnaveni river story.", "videoViewCount": 1150000, "timestamp": "2025-07-01T10:00:00Z", "title": "కృష్ణవేణి"},
    {"url": "https://www.instagram.com/p/C32", "caption": "Karnataka Temples heritage.", "videoViewCount": 1020000, "timestamp": "2025-07-04T10:00:00Z", "title": "KARNATAKA TEMPLES"},
    {"url": "https://www.instagram.com/p/C33", "caption": "Godavari River course.", "videoViewCount": 1580000, "timestamp": "2025-07-08T10:00:00Z", "title": "गोदावरी"},
    {"url": "https://www.instagram.com/p/C34", "caption": "Jharkhand state profile.", "videoViewCount": 790000, "timestamp": "2025-07-10T10:00:00Z", "title": "झारखंड"},
    {"url": "https://www.instagram.com/p/C35", "caption": "Assam state profile.", "videoViewCount": 1190000, "timestamp": "2026-09-08T10:00:00Z", "title": "অসম"}  # Published in last 7 days!
]

# OBSERVED — read off the live @dailygeomap profile on 13 Sep 2026 and recorded
# in the competitor teardown. ~20 reels sampled with their view counts. These
# are real numbers, unlike SAMPLE_SEED_POSTS below, so they are what the seed
# falls back to when no Apify token is configured.
#
# Two caveats carried over from the teardown: the counts are not age-adjusted,
# and three of the posts are the account's own pinned picks. The two most
# recent posts are dated inside the 7-day window on purpose — Stage 0's own
# cutoff rule then excludes them from the medians, which is exactly right for
# a reel that has not finished earning its views.
TEARDOWN_OBSERVED_POSTS = [
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Temples of Kerala", "videoViewCount": 7100000, "timestamp": "2026-05-02T10:00:00Z", "title": "TEMPLES OF KERALA"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Indian Mountains", "videoViewCount": 4400000, "timestamp": "2026-05-16T10:00:00Z", "title": "INDIAN MOUNTAINS"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "State Fish of India", "videoViewCount": 3700000, "timestamp": "2026-06-01T10:00:00Z", "title": "STATE FISH"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Indian Rivers", "videoViewCount": 1600000, "timestamp": "2026-06-14T10:00:00Z", "title": "INDIAN RIVERS"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "State Animals of India", "videoViewCount": 1300000, "timestamp": "2026-06-28T10:00:00Z", "title": "STATE ANIMALS"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Kaveri, the river as a woman", "videoViewCount": 1200000, "timestamp": "2026-07-05T10:00:00Z", "title": "KAVERI"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Karnataka", "videoViewCount": 1100000, "timestamp": "2026-07-12T10:00:00Z", "title": "ಕರ್ನಾಟಕ"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Telangana Temples", "videoViewCount": 1000000, "timestamp": "2026-07-19T10:00:00Z", "title": "TELANGANA TEMPLES"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Zonal Councils of India", "videoViewCount": 526000, "timestamp": "2026-07-26T10:00:00Z", "title": "ZONAL COUNCILS"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Temples of Maharashtra", "videoViewCount": 363000, "timestamp": "2026-08-02T10:00:00Z", "title": "TEMPLES OF MAHARASHTRA"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Spain", "videoViewCount": 271000, "timestamp": "2026-08-09T10:00:00Z", "title": "SPAIN"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Flash Floods", "videoViewCount": 193000, "timestamp": "2026-08-16T10:00:00Z", "title": "FLASH FLOODS"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Periyar", "videoViewCount": 114000, "timestamp": "2026-08-20T10:00:00Z", "title": "പെരിയാർ നദി"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "India Post", "videoViewCount": 88800, "timestamp": "2026-08-24T10:00:00Z", "title": "INDIA POST"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Tapi", "videoViewCount": 78500, "timestamp": "2026-08-28T10:00:00Z", "title": "तापी"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "The Ahom kingdom", "videoViewCount": 72200, "timestamp": "2026-08-31T10:00:00Z", "title": "AHOM"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Mahanadi", "videoViewCount": 68800, "timestamp": "2026-09-03T10:00:00Z", "title": "महानदी"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "National Symbols of India", "videoViewCount": 43000, "timestamp": "2026-09-10T10:00:00Z", "title": "NATIONAL SYMBOLS"},
    {"url": "https://www.instagram.com/dailygeomap/", "caption": "Russia", "videoViewCount": 34000, "timestamp": "2026-09-12T10:00:00Z", "title": "RUSSIA"},
    # Added from GeoGlobeTales (YouTube)
    {"url": "https://www.youtube.com/@GeoGlobeTales/shorts", "caption": "Why People Settled the Most Remote Island", "videoViewCount": 1000000, "timestamp": "2026-08-01T10:00:00Z", "title": "MOST REMOTE ISLAND"},
    {"url": "https://www.youtube.com/@GeoGlobeTales/shorts", "caption": "Why Virginia Split Into Two States", "videoViewCount": 2100000, "timestamp": "2026-08-10T10:00:00Z", "title": "VIRGINIA SPLIT"},
    {"url": "https://www.youtube.com/@GeoGlobeTales/shorts", "caption": "The True Size of Hawaii", "videoViewCount": 1400000, "timestamp": "2026-08-20T10:00:00Z", "title": "TRUE SIZE OF HAWAII"},
    {"url": "https://www.youtube.com/@GeoGlobeTales/shorts", "caption": "Why the Great Lakes Are So Great", "videoViewCount": 1300000, "timestamp": "2026-09-01T10:00:00Z", "title": "GREAT LAKES"},
    {"url": "https://www.youtube.com/@GeoGlobeTales/shorts", "caption": "Why is the Netherlands Called Holland?", "videoViewCount": 818000, "timestamp": "2026-09-10T10:00:00Z", "title": "NETHERLANDS HOLLAND"},
]

APIFY_ACTOR = "apify~instagram-scraper"
APIFY_POLL_ATTEMPTS = 60
APIFY_POLL_SECONDS = 10


def _write_seed(posts, source, seed_file, note=""):
    payload = {
        "data_source": source,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "post_count": len(posts),
        "note": note,
        "posts": posts,
    }
    with open(seed_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[Stage 0] seed.json written: {len(posts)} posts, source={source}")
    return seed_file


def scrape_apify(token, handle="dailygeomap", limit=300):
    """Run the Apify actor and WAIT for its dataset.

    The previous version fired a run and never read the result, then
    overwrote seed.json with the hardcoded sample anyway — so a configured
    token changed nothing.
    """
    start = requests.post(
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/runs",
        params={"token": token},
        json={
            "directUrls": [f"https://www.instagram.com/{handle}/"],
            "resultsType": "posts",
            "resultsLimit": limit,
            "addParentData": False,
        },
        timeout=60,
    )
    start.raise_for_status()
    run = start.json()["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    print(f"[Stage 0] Apify run {run_id} started; waiting for the dataset...")

    for attempt in range(1, APIFY_POLL_ATTEMPTS + 1):
        status = requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            params={"token": token}, timeout=60,
        )
        status.raise_for_status()
        state = status.json()["data"]["status"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended as {state}")
        print(f"[Stage 0]   {state} ({attempt}/{APIFY_POLL_ATTEMPTS})")
        time.sleep(APIFY_POLL_SECONDS)
    else:
        raise RuntimeError(f"Apify run {run_id} did not finish in time")

    items = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        params={"token": token, "clean": "true", "format": "json"},
        timeout=120,
    )
    items.raise_for_status()
    posts = items.json()
    if not posts:
        raise RuntimeError(f"Apify dataset {dataset_id} came back empty")

    normalised = [
        {
            "url": p.get("url"),
            "caption": p.get("caption", ""),
            "videoViewCount": p.get("videoViewCount") or p.get("videoPlayCount") or 0,
            "timestamp": p.get("timestamp", ""),
            "title": (p.get("caption", "").strip().split("\n")[0])[:60].upper(),
        }
        for p in posts
    ]
    print(f"[Stage 0] Apify returned {len(normalised)} posts")
    return normalised


def fetch_seed_data():
    """Populate seed.json, recording honestly where the numbers came from."""
    seed_file = os.path.join(os.path.dirname(__file__), "seed.json")
    token = os.getenv("APIFY_API_TOKEN", "").strip()

    # Your own published reels first: free, and about your audience rather
    # than someone else's. Only once there are enough of them to have a
    # median worth trusting — see performance.MIN_ROWS_TO_WEIGH.
    try:
        from core import performance
        own = performance.as_seed_posts()
    except Exception as e:  # noqa: BLE001 — never block the seed on this
        print(f"[Stage 0] own-channel ledger unreadable: {type(e).__name__}: {e}")
        own = []
    if own:
        return _write_seed(
            own, "own_channel", seed_file,
            note=f"View counts read back from {len(own)} of your own published "
                 f"reels via the YouTube and Instagram APIs. These are the only "
                 f"numbers that describe your audience rather than a competitor's.",
        )

    if token:
        try:
            posts = scrape_apify(token)
            return _write_seed(posts, "apify_scrape", seed_file,
                               note="Observed view counts from the Apify dataset.")
        except Exception as e:
            print(f"[Stage 0] Apify scrape failed ({type(e).__name__}: {e}).")
    else:
        print("[Stage 0] No APIFY_API_TOKEN set.")

    print("[Stage 0] Falling back to the 13 Sep 2026 teardown observations.")
    return _write_seed(
        TEARDOWN_OBSERVED_POSTS, "teardown_observed", seed_file,
        note="View counts read off the live @dailygeomap profile on 13 Sep 2026. "
             "Real observations, but a ~20-post sample, not age-adjusted, and "
             "including the account's pinned picks. Better than the invented "
             "priors in SAMPLE_SEED_POSTS; weaker than a full Apify scrape.",
    )


def load_seed_posts(seed_file=None):
    """Read seed.json in either the new wrapped shape or the old bare list."""
    seed_file = seed_file or os.path.join(os.path.dirname(__file__), "seed.json")
    with open(seed_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data, "legacy_bare_list"
    return data.get("posts", []), data.get("data_source", "unknown")


if __name__ == "__main__":
    fetch_seed_data()
