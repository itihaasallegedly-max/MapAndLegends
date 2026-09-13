import json
import random
import hashlib
import pathlib

BASE_DIR = pathlib.Path(__file__).parent
LEDGER = BASE_DIR / "used_topics.sha1"
TOPICS_FILE = BASE_DIR / "topics.json"

def slug_hash(t):
    """Generates SHA1 hash of normalised topic string for dedup ledger."""
    return hashlib.sha1(t["topic"].lower().strip().encode()).hexdigest()

def draw_daily_topic(mark_used=False):
    """
    Weighted sample draw from topics.json backlog based on series/topic weights.
    Filters out any topic hash present in used_topics.sha1.
    If mark_used is True, records the hash immediately to used_topics.sha1.
    """
    if not TOPICS_FILE.exists():
        raise SystemExit("topics.json missing — run Stage 0 prompt1 script first.")

    used = set(LEDGER.read_text().split()) if LEDGER.exists() else set()
    data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    backlog = data.get("backlog", [])

    pool = [t for t in backlog if slug_hash(t) not in used]
    if not pool:
        raise SystemExit("Backlog exhausted — rerun Stage 0 Prompt 1 to generate fresh topics.")

    weights = [t.get("weight", 0.5) for t in pool]
    pick = random.choices(pool, weights=weights, k=1)[0]
    pick["slug_hash"] = slug_hash(pick)

    if mark_used:
        record_topic_used(pick)

    print(
        f"[Stage 2 Draw] {pick['topic']!r} | subject={pick.get('subject', pick['topic'])!r} "
        f"| series={pick['series']} | weight={pick['weight']} "
        f"| {len(pool)} of {len(backlog)} topics still unused"
    )
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
