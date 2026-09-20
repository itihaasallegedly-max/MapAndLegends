import os
import subprocess
import glob
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
YT_DLP_BIN = os.path.join(BASE_DIR, "venv", "bin", "yt-dlp")

def download_video(url: str, out_dir: str = None) -> str:
    """
    Downloads a YouTube video to the specified directory and returns the file path.
    """
    if out_dir is None:
        out_dir = DOWNLOADS_DIR
        os.makedirs(out_dir, exist_ok=True)
        # Clean previous competitor downloads so we don't pile up
        existing = glob.glob(os.path.join(out_dir, "competitor_*.mp4"))
        for f in existing:
            os.remove(f)
        out_filename = "competitor_video"
    else:
        os.makedirs(out_dir, exist_ok=True)
        out_filename = "original_video"
        
    out_path = os.path.join(out_dir, f"{out_filename}.%(ext)s")
    
    print(f"[CompetitorDownload] Fetching video from {url}...")
    
    cmd = [
        YT_DLP_BIN,
        url,
        "--format", "mp4",
        "--extractor-args", "youtube:player_client=android",
        "--output", out_path,
        "--force-overwrites"
    ]
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed (exit {res.returncode}) for {url}:\n{res.stderr}")
        
    # Find the downloaded file
    downloaded = glob.glob(os.path.join(out_dir, f"{out_filename}.mp4"))
    if not downloaded:
        raise FileNotFoundError(f"Could not find the downloaded video in {out_dir}")
        
    print(f"[CompetitorDownload] Successfully downloaded to {downloaded[0]}")
    return downloaded[0]

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(download_video(sys.argv[1]))
    else:
        print("Usage: python download_competitor.py <url>")
