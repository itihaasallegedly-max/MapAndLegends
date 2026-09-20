import os
import glob
import subprocess
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF_DIR = os.path.join(BASE_DIR, "brand", "references")
YT_DLP_BIN = os.path.join(BASE_DIR, "venv", "bin", "yt-dlp")

def sync_references():
    competitors = [
        "https://www.youtube.com/@DailyGeoMap",
        "https://www.youtube.com/@GeoGlobeTales"
    ]
    
    os.makedirs(REF_DIR, exist_ok=True)
    
    for channel_url in competitors:
        print(f"[SyncReferences] Fetching latest shorts from {channel_url}...")
        
        # Download the 2 most recent shorts
        cmd = [
            YT_DLP_BIN,
            channel_url,
            "--match-filter", "original_url!*=/watch?",  # Filter out regular videos if possible, mostly targets shorts
            "--playlist-end", "2",
            "--format", "mp4",
            "--output", os.path.join(REF_DIR, "%(title)s.%(ext)s"),
            "--no-overwrites"
        ]
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[SyncReferences] WARNING: yt-dlp failed (exit {res.returncode}) for {channel_url}:\n{res.stderr}")
        else:
            print(f"[SyncReferences] Successfully synced latest references from {channel_url}.")
    
    # Clean up older reference videos to prevent the folder from growing infinitely.
    # Keep the 6 most recently modified ones (since we have 2 competitors now).
    videos = glob.glob(os.path.join(REF_DIR, "*.mp4"))
    videos.sort(key=os.path.getmtime, reverse=True)
    
    if len(videos) > 6:
        for old_video in videos[6:]:
            print(f"[SyncReferences] Cleaning up old reference: {os.path.basename(old_video)}")
            os.remove(old_video)

if __name__ == "__main__":
    sync_references()
