import json
import os
import random
import hashlib
import pathlib

BASE_DIR = pathlib.Path(__file__).parent

# A weight is an estimate of how well a topic will do. Drawing in direct
# proportion to it publishes the weakest topics most of the time simply
# because there are more of them — with the current backlog, single-subject
# topics would take two draws in three even though the seed says a collection
# is worth an order of magnitude more. Raising the weights to a power
# concentrates the draw on the topics worth making while leaving every topic
# reachable, so the backlog still drains instead of looping on one series.
#   1 = proportional (the old behaviour), 2 = the default tilt, 0 = uniform.
DRAW_WEIGHT_EXPONENT = float(os.getenv("DRAW_WEIGHT_EXPONENT", "2"))
LEDGER = BASE_DIR / "used_topics.sha1"
TOPICS_FILE = BASE_DIR / "topics.json"

def slug_hash(t):
    """Generates SHA1 hash of normalised topic string for dedup ledger."""
    return hashlib.sha1(t["topic"].lower().strip().encode()).hexdigest()

def draw_daily_topic(mark_used=False):
    """
    Pops the first URL from competitor_urls.json.
    If mark_used is True, records the hash to used_topics.sha1.
    """
    comp_file = BASE_DIR / "competitor_urls.json"
    if not comp_file.exists():
        raise SystemExit("competitor_urls.json missing — please add URLs.")

    used = set(LEDGER.read_text().split()) if LEDGER.exists() else set()
    urls = json.loads(comp_file.read_text(encoding="utf-8"))

    pool = [u for u in urls if hashlib.sha1(u.strip().encode()).hexdigest() not in used]
    if not pool:
        raise SystemExit("Backlog exhausted — add more URLs to competitor_urls.json.")

    pick_url = pool[0]
    pick = {
        "topic": f"Competitor Analysis: {pick_url}",
        "url": pick_url,
        "subject": "Competitor Analysis",
        "series": "Competitor Rewrite",
        "weight": 1.0
    }
    pick["slug_hash"] = hashlib.sha1(pick_url.strip().encode()).hexdigest()

    if mark_used:
        record_topic_used(pick)

    print(f"[Stage 2 Draw] Picked Competitor URL: {pick_url}")
    return pick

def record_topic_used(pick):
    """Appends topic SHA1 hash to used_topics.sha1 ledger."""
    h = slug_hash(pick) if isinstance(pick, dict) else pick
    used = set(LEDGER.read_text().split()) if LEDGER.exists() else set()
    if h not in used:
        with LEDGER.open("a") as f:
            f.write(h + "\n")
        print(f"[Ledger] Recorded topic hash {h[:8]}... to used_topics.sha1")

if __name__ == "__main__":
    drawn = draw_daily_topic(mark_used=False)
    print("Drawn Topic Spec:", json.dumps(drawn, indent=2))
