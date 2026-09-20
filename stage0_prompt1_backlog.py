import datetime
import json
import os

from dotenv import load_dotenv

from stage0_collections import build_collection_topics
from stage0_seed import load_seed_posts

load_dotenv()

# Complete list of Indian States & Union Territories for full natural expansion
INDIAN_STATES_AND_UTS = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal", "Andaman and Nicobar", "Chandigarh", "Dadra and Nagar Haveli",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"
]

INDIAN_RIVERS = [
    "Ganga", "Yamuna", "Godavari", "Krishna", "Kaveri", "Narmada", "Tapi", "Mahanadi",
    "Brahmaputra", "Indus", "Saraswati", "Sabarmati", "Mahi", "Luni", "Subarnarekha",
    "Baitarani", "Brahmani", "Pennar", "Periyar", "Vaidai", "Sharavathi", "Mandovi",
    "Zuari", "Tapti", "Damodar", "Kosi", "Gandak", "Ghaghara", "Chambal", "Betwa",
    "Ken", "Son", "Tons", "Hooghly", "Teesta", "Barak", "Manas", "Sankosh"
]

WORLD_COUNTRIES = [
    "Japan", "Greece", "Argentina", "Spain", "Germany", "Indonesia", "Italy", "France",
    "Brazil", "Egypt", "Canada", "Australia", "South Korea", "Vietnam", "Thailand",
    "Mexico", "South Africa", "Russia", "United Kingdom", "Turkey", "Iran", "Saudi Arabia",
    "Norway", "Iceland", "Switzerland", "Netherlands", "Peru", "Chile", "New Zealand"
]

CIVIC_TOPICS = [
    "National Anthem", "National Flag Code", "Zonal Councils", "Panchayati Raj System",
    "India Post Network", "Census Demographics", "Pin Code System", "Railway Zones",
    "Major Sea Ports", "National Highways", "UNESCO Heritage Sites", "Biosphere Reserves",
    "Ramsar Wetland Sites", "Tiger Reserves", "Elephant Corridors", "National Parks",
    "State Birds", "State Flowers", "State Trees", "State Emblems", "State Dances",
    "Major Mountain Passes", "Highest Waterfalls", "Major River Dams", "Glaciers of Himalayas",
    "Volcanoes of India", "Salt Deserts", "Coral Reefs", "Coastal Plains", "Plateaus of India",
    "Ancient Universities", "Forts of India", "Rock Cut Caves", "Hill Stations", "Border Line Names"
]

# --- shape, not theme -------------------------------------------------------
#
# The series buckets below sort posts by SUBJECT (rivers, temples, states).
# The teardown found that what actually predicts views is SHAPE: a collection
# ("every state's fish", "the temples of Kerala") against a single subject
# ("the Tapi", "the Ahom kingdom"). The gap it measured was an order of
# magnitude, which is larger than any gap between the themes. So the seed is
# classified on both axes, and the second one sets the Collections weight.
SET_MARKERS = (
    "state animal", "state bird", "state tree", "state flower", "state fish",
    "state dance", "state capital", "state emblem", "state symbol",
    "symbols", "temples", "shrines", "forts", "rivers", "mountains", "peaks",
    "waterfalls", "dams", "councils", "districts", "dances", "islands",
    "parks", "reserves", "capitals", "languages", "festivals", "currencies",
    "every ", "all the ", "of india", "of indian",
)


def is_set_shaped(post):
    """Does this post walk a list, or dwell on one subject?"""
    blob = f"{post.get('title', '')} {post.get('caption', '')}".lower()
    return any(marker in blob for marker in SET_MARKERS)


def _median(values, default=0.0):
    if not values:
        return default
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def generate_backlog():
    """
    Executes Prompt 1 logic:
    1. Reads seed.json
    2. Filters out posts published within last 7 days from median view calculations.
    3. Clusters into series, calculates median views & normalized 0-1 weights.
    4. Expands to 500+ backlog items.
    5. Saves to topics.json.
    """
    seed_path = os.path.join(os.path.dirname(__file__), "seed.json")
    topics_path = os.path.join(os.path.dirname(__file__), "topics.json")

    seed_data, data_source = load_seed_posts(seed_path)
    if data_source == "hand_seeded_priors":
        print("[Stage 0] NOTE seed is hand-seeded priors — the view counts are "
              "invented, so the weights below are editorial guesses.")
    elif data_source == "teardown_observed":
        print("[Stage 0] NOTE seed is the 13 Sep 2026 teardown — real observed "
              "views, but a ~20-post sample and not age-adjusted.")
    elif data_source != "apify_scrape":
        print(f"[Stage 0] NOTE unrecognised seed data_source={data_source!r}.")

    # 7-day cutoff rule
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=7)

    # Group views by series
    series_views = {
        "Sacred Rivers": [],
        "Temples & Heritage": [],
        "State Profiles": [],
        "Symbols & Civics": [],
        "Global Geography": []
    }

    set_views, single_views = [], []

    seed_titles = set()
    for post in seed_data:
        title = post.get("title", "").upper()
        seed_titles.add(title)
        views = post.get("videoViewCount", 0)
        ts_str = post.get("timestamp", "")
        
        # Check if published in last 7 days
        is_recent = False
        if ts_str:
            try:
                dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                is_recent = dt >= cutoff
            except ValueError:
                # A post with an unparseable timestamp is counted, not dropped
                # silently — otherwise the 7-day exclusion quietly widens.
                print(f"[Stage 0] WARNING unparseable timestamp {ts_str!r} on {title!r}")

        if not is_recent:
            (set_views if is_set_shaped(post) else single_views).append(views)
            if any(k in title for k in ["RIVER", "महानदी", "तापी", "गोदावरी", "KAVERI", "कवि", "ஆறுகள்"]):
                series_views["Sacred Rivers"].append(views)
            elif any(k in title for k in ["TEMPLE", "तिरुवनंतपुरम", "PADMANABHASWAMY"]):
                series_views["Temples & Heritage"].append(views)
            elif any(k in title for k in ["STATE", "उत्तर प्रदेश", "తెలంగాణ", "ಕರ್ನಾಟಕ", "गुजरात", "मध्य प्रदेश", "झारखंड", "असम", "ASSAM"]):
                series_views["State Profiles"].append(views)
            elif any(k in title for k in ["SYMBOL", "ANIMAL", "FISH", "POST", "COUNCIL", "POPULATION"]):
                series_views["Symbols & Civics"].append(views)
            else:
                series_views["Global Geography"].append(views)

    # Calculate medians and max median for normalization
    series_medians = {}
    for s_name, views in series_views.items():
        if views:
            views.sort()
            n = len(views)
            med = views[n // 2] if n % 2 != 0 else (views[n // 2 - 1] + views[n // 2]) / 2
        else:
            med = 750000
        series_medians[s_name] = med

    # Collections gets its own series, weighted off the set-shaped posts
    # rather than off a theme. With no set-shaped posts in the seed it falls
    # back to the best theme median, so it is never silently zeroed.
    set_median = _median(set_views)
    single_median = _median(single_views)
    if set_median:
        series_medians["Collections"] = set_median
        series_views["Collections"] = set_views
    set_lift = round(set_median / single_median, 2) if set_median and single_median else None

    max_med = max(series_medians.values()) if series_medians else 1000000

    # Build series metadata with normalized weights
    series_meta = []
    for s_name, med in series_medians.items():
        weight = round(med / max_med, 3)
        series_meta.append({
            "name": s_name,
            "template": get_template_for_series(s_name),
            "language": "english",
            "observed_median_views": int(med),
            "observed_n": len(series_views[s_name]),
            "weight": weight
        })

    # Generate 500+ backlog items (natural expansion, no verbatim title reuse)
    backlog = []

    # 1. State Profiles & Districts (36 States & UTs x 4 variations = 144)
    w_state = series_medians["State Profiles"] / max_med
    for state in INDIAN_STATES_AND_UTS:
        backlog.append({
            "topic": f"Geography and Facts of {state}",
            "series": "State Profiles",
            "title_en": f"{state.upper()}",
            "weight": round(w_state, 3)
        })
        backlog.append({
            "topic": f"Districts and Regions of {state}",
            "series": "State Profiles",
            "title_en": f"DISTRICTS OF {state.upper()[:12]}",
            "weight": round(w_state * 0.95, 3)
        })
        backlog.append({
            "topic": f"Highest Peaks and Rivers of {state}",
            "series": "State Profiles",
            "title_en": f"GEOGRAPHY OF {state.upper()[:12]}",
            "weight": round(w_state * 0.9, 3)
        })
        backlog.append({
            "topic": f"Cultural Symbols and Food of {state}",
            "series": "State Profiles",
            "title_en": f"CULTURE OF {state.upper()[:12]}",
            "weight": round(w_state * 0.85, 3)
        })

    # 2. Temples & Heritage (36 States & UTs x 3 variations = 108)
    w_temple = series_medians["Temples & Heritage"] / max_med
    for state in INDIAN_STATES_AND_UTS:
        backlog.append({
            "topic": f"Ancient Temples and Heritage of {state}",
            "series": "Temples & Heritage",
            "title_en": f"TEMPLES OF {state.upper()[:12]}",
            "weight": round(w_temple, 3)
        })
        backlog.append({
            "topic": f"Sacred Monasteries and Shrines of {state}",
            "series": "Temples & Heritage",
            "title_en": f"SHRINES OF {state.upper()[:12]}",
            "weight": round(w_temple * 0.9, 3)
        })
        backlog.append({
            "topic": f"Historical Forts and Architecture of {state}",
            "series": "Temples & Heritage",
            "title_en": f"FORTS OF {state.upper()[:12]}",
            "weight": round(w_temple * 0.88, 3)
        })

    # 3. Sacred Rivers (38 Rivers x 3 variations = 114)
    w_river = series_medians["Sacred Rivers"] / max_med
    for river in INDIAN_RIVERS:
        backlog.append({
            "topic": f"Course and Origin of River {river}",
            "series": "Sacred Rivers",
            "title_en": f"RIVER {river.upper()}",
            "weight": round(w_river, 3)
        })
        backlog.append({
            "topic": f"Dams and Tributaries of River {river}",
            "series": "Sacred Rivers",
            "title_en": f"DAMS OF {river.upper()}",
            "weight": round(w_river * 0.92, 3)
        })
        backlog.append({
            "topic": f"Mythology and Sacred Ghats of {river}",
            "series": "Sacred Rivers",
            "title_en": f"GHATS OF {river.upper()}",
            "weight": round(w_river * 0.95, 3)
        })

    # 4. Symbols & Civics (35 Topics x 3 variations = 105)
    w_civic = series_medians["Symbols & Civics"] / max_med
    for civic in CIVIC_TOPICS:
        backlog.append({
            "topic": f"Secrets of {civic} in India",
            "series": "Symbols & Civics",
            "title_en": f"{civic.upper()[:16]}",
            "weight": round(w_civic, 3)
        })
        backlog.append({
            "topic": f"Competitive Exam Facts: {civic}",
            "series": "Symbols & Civics",
            "title_en": f"FACTS {civic.upper()[:12]}",
            "weight": round(w_civic * 0.9, 3)
        })
        backlog.append({
            "topic": f"Map Overview of {civic}",
            "series": "Symbols & Civics",
            "title_en": f"MAP OF {civic.upper()[:12]}",
            "weight": round(w_civic * 0.85, 3)
        })

    # 5. Global Geography (29 Countries x 3 variations = 87)
    w_global = series_medians["Global Geography"] / max_med
    for country in WORLD_COUNTRIES:
        backlog.append({
            "topic": f"Unseen Geography of {country}",
            "series": "Global Geography",
            "title_en": f"{country.upper()}",
            "weight": round(w_global, 3)
        })
        backlog.append({
            "topic": f"Borders and Rivers of {country}",
            "series": "Global Geography",
            "title_en": f"MAP OF {country.upper()}",
            "weight": round(w_global * 0.85, 3)
        })
        backlog.append({
            "topic": f"Wonders and Capital of {country}",
            "series": "Global Geography",
            "title_en": f"WONDERS OF {country.upper()[:10]}",
            "weight": round(w_global * 0.88, 3)
        })

    # 6. Collections — the shape the teardown says carries the account. Built
    #    last so the single-subject expansions above keep the topic strings
    #    used_topics.sha1 already hashes, and appended rather than replacing
    #    them: a set topic outranks a single one by weight, not by deletion.
    w_set = series_medians.get("Collections", max_med) / max_med
    collections = build_collection_topics(w_set)
    backlog.extend(collections)

    attach_subjects(backlog)

    # Final topics.json output object
    output_obj = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "weight_basis": data_source,
        "weight_basis_note": (
            "Weights are normalised series medians from seed.json. With "
            "data_source != 'apify_scrape' they are hand-set priors, not measurements."
        ),
        "shape_basis": {
            "set_median_views": int(set_median) if set_median else None,
            "single_median_views": int(single_median) if single_median else None,
            "set_lift": set_lift,
            "note": (
                "Median views of collection-shaped posts against single-subject "
                "posts in the same seed. The Collections series weight comes "
                "from the first of these. A lift well above 1 is the teardown's "
                "central finding; it is a ~20-post sample, so treat it as "
                "directional."
            ),
        },
        "series": series_meta,
        "backlog": backlog,
        "set_topic_count": len(collections),
        "total_backlog_count": len(backlog)
    }

    with open(topics_path, "w", encoding="utf-8") as f:
        json.dump(output_obj, f, indent=2, ensure_ascii=False)

    print(f"[Stage 0] topics.json: {len(backlog)} topics across {len(series_meta)} "
          f"series, {len(collections)} of them set topics")
    if set_lift:
        print(f"[Stage 0] set-shaped posts out-perform single-subject ones "
              f"{set_lift}x in the seed ({int(set_median):,} vs {int(single_median):,} "
              f"median views)")
    return topics_path

def attach_subjects(backlog):
    """Give every backlog item the noun the script should be *about*.

    `topic` is a framing phrase ("Map Overview of Tiger Reserves"), which the
    script prompt used to drop into sentences as if it were a subject —
    producing lines like "Map Overview of Tiger Reserves plays a major role in
    regional climate". `subject` is the entity itself.

    The `topic` strings are deliberately left untouched: used_topics.sha1
    hashes them, so changing one would resurrect an already-published topic.
    """
    entities = (
        [(s, s) for s in INDIAN_STATES_AND_UTS]
        + [(r, f"the {r} river") for r in INDIAN_RIVERS]
        + [(c, c) for c in WORLD_COUNTRIES]
        + [(t, f"{t} in India") for t in CIVIC_TOPICS]
    )
    # Longest name first so "Andhra Pradesh" wins over "Andhra".
    entities.sort(key=lambda pair: len(pair[0]), reverse=True)

    for item in backlog:
        if item.get("subject"):
            continue          # set topics name their own subject; don't guess over it
        topic = item["topic"]
        item["subject"] = next(
            (subject for name, subject in entities if name.lower() in topic.lower()),
            topic,
        )
    return backlog


def get_template_for_series(s_name):
    templates = {
        "Sacred Rivers": "River {river}",
        "Temples & Heritage": "Temples of {state}",
        "State Profiles": "{state} State Facts",
        "Symbols & Civics": "{symbol} Secrets",
        "Global Geography": "{country} Unseen",
        "Collections": "The {attribute} of every {group}"
    }
    return templates.get(s_name, "{topic}")

if __name__ == "__main__":
    generate_backlog()
