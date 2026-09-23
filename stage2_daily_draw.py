import os
import random
import hashlib
import pathlib
import subprocess

BASE_DIR = pathlib.Path(__file__).parent
LEDGER = BASE_DIR / "used_topics.sha1"
YT_DLP_BIN = BASE_DIR / "venv" / "bin" / "yt-dlp"

def slug_hash(t):
    """Generates SHA1 hash of normalised topic string for dedup ledger."""
    return hashlib.sha1(t["topic"].lower().strip().encode()).hexdigest()

def fetch_unseen_competitor_shorts():
    competitors = [
        "https://www.youtube.com/@GeoGlobeTales/shorts",
        "https://www.youtube.com/@DailyGeoMap/shorts"
    ]
    
    used_hashes = set(LEDGER.read_text().split()) if LEDGER.exists() else set()
    unseen_urls = []
    
    for channel_url in competitors:
        print(f"[DailyDraw] Scanning {channel_url} for new videos...")
        cmd = [
            str(YT_DLP_BIN),
            "--flat-playlist",
            "--playlist-end", "15",
            "--print", "original_url",
            channel_url
        ]
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[DailyDraw] WARNING: yt-dlp failed for {channel_url}")
            continue
            
        urls = res.stdout.strip().split("\n")
        
        for url in urls:
            url = url.strip()
            if not url or "shorts/" not in url:
                continue
                
            url_hash = hashlib.sha1(url.encode()).hexdigest()
            if url_hash not in used_hashes:
                unseen_urls.append(url)
                
    return unseen_urls

def competitor_title(url):
    """The competitor Short's own title, so Prompt 2 is told what the video is about
    (and the output folder gets a readable name) instead of "Competitor Analysis"."""
    try:
        res = subprocess.run([str(YT_DLP_BIN), "--skip-download", "--no-warnings",
                              "--extractor-args", "youtube:player_client=android",
                              "--print", "title", url],
                             capture_output=True, text=True, timeout=60)
        title = res.stdout.strip().splitlines()[0] if res.returncode == 0 and res.stdout.strip() else ""
    except Exception:  # noqa: BLE001 — a title is nice to have, never worth a failed run
        title = ""
    import re
    title = re.sub(r"#\S+", "", title)          # drop hashtags from the title
    return re.sub(r"\s+", " ", title).strip(" -|:")


def draw_daily_topic(mark_used=False):
    """
    Automatically scrapes the latest competitor Shorts and picks one that hasn't been used.
    """
    unseen = fetch_unseen_competitor_shorts()
    
    if not unseen:
        raise SystemExit("Backlog exhausted — no unseen videos found on competitor channels.")

    pick_url = unseen[0]  # Just take the freshest one
    title = competitor_title(pick_url)
    vid = pick_url.rstrip("/").split("/")[-1]
    pick = {
        "topic": f"{title} ({vid})" if title else f"Competitor Short {vid}",
        "url": pick_url,
        "subject": title or "the geography topic of the attached competitor video",
        "series": "Competitor Rewrite",
        "weight": 1.0
    }
    pick["slug_hash"] = hashlib.sha1(pick_url.strip().encode()).hexdigest()

    if mark_used:
        record_topic_used(pick)

    print(f"[Stage 2 Draw] Dynamically Picked Competitor URL: {pick_url}")
    print(f"[Stage 2 Draw] Competitor title: {title or '(unavailable)'}")
    return pick

def record_topic_used(pick):
    """Appends topic SHA1 hash to used_topics.sha1 ledger."""
    if isinstance(pick, dict):
        h = pick.get("slug_hash") or slug_hash(pick)
    else:
        h = pick
    used = set(LEDGER.read_text().split()) if LEDGER.exists() else set()
    if h not in used:
        with LEDGER.open("a") as f:
            f.write(h + "\n")
        print(f"[Ledger] Recorded topic hash {h[:8]}... to used_topics.sha1")

if __name__ == "__main__":
    import json
    drawn = draw_daily_topic(mark_used=False)
    print("Drawn Topic Spec:", json.dumps(drawn, indent=2))
