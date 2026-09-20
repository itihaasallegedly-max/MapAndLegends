import os
import json
import subprocess
import hashlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YT_DLP_BIN = os.path.join(BASE_DIR, "venv", "bin", "yt-dlp")
COMPETITOR_URLS_FILE = os.path.join(BASE_DIR, "competitor_urls.json")
LEDGER_FILE = os.path.join(BASE_DIR, "used_topics.sha1")

def fetch_latest_competitor_shorts():
    competitors = [
        "https://www.youtube.com/@GeoGlobeTales/shorts",
        "https://www.youtube.com/@DailyGeoMap/shorts"
    ]
    
    # Load existing queue and ledger
    if os.path.exists(COMPETITOR_URLS_FILE):
        with open(COMPETITOR_URLS_FILE, "r") as f:
            queue = json.load(f)
    else:
        queue = []
        
    used_hashes = set()
    if os.path.exists(LEDGER_FILE):
        with open(LEDGER_FILE, "r") as f:
            used_hashes = set(f.read().split())
            
    queue_hashes = {hashlib.sha1(u.strip().encode()).hexdigest() for u in queue}
    
    new_urls_added = 0
    
    for channel_url in competitors:
        print(f"[SyncReferences] Fetching latest shorts URLs from {channel_url}...")
        
        # Use yt-dlp to extract just the URLs of the latest 15 shorts (fast, no downloading video)
        cmd = [
            YT_DLP_BIN,
            "--flat-playlist",
            "--playlist-end", "15",
            "--print", "original_url",
            channel_url
        ]
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[SyncReferences] WARNING: yt-dlp failed for {channel_url}:\n{res.stderr}")
            continue
            
        urls = res.stdout.strip().split("\n")
        
        for url in urls:
            url = url.strip()
            if not url or "shorts/" not in url:
                continue
                
            url_hash = hashlib.sha1(url.encode()).hexdigest()
            
            # Add to queue only if it's not already in the queue and hasn't been generated before
            if url_hash not in used_hashes and url_hash not in queue_hashes:
                queue.append(url)
                queue_hashes.add(url_hash)
                new_urls_added += 1
                print(f"[SyncReferences] Added new competitor video to queue: {url}")
                
    if new_urls_added > 0:
        with open(COMPETITOR_URLS_FILE, "w") as f:
            json.dump(queue, f, indent=2)
        print(f"[SyncReferences] Done! Added {new_urls_added} fresh videos to the queue.")
    else:
        print(f"[SyncReferences] Done. No new unseen videos found on competitor channels.")

if __name__ == "__main__":
    fetch_latest_competitor_shorts()
