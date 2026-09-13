import json
import os
import subprocess

def fetch_references(limit=3):
    """
    Reads seed.json and uses yt-dlp to download the top `limit` videos 
    into brand/references/ to be used as style references by Gemini.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_path = os.path.join(base_dir, "seed.json")
    refs_dir = os.path.join(base_dir, "brand", "references")
    os.makedirs(refs_dir, exist_ok=True)
    
    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    posts = data.get("posts", [])
    urls_to_download = [p["url"] for p in posts if p.get("url")]
    urls_to_download = urls_to_download[:limit]
    
    print(f"Downloading {len(urls_to_download)} reference videos to {refs_dir}...")
    
    for i, url in enumerate(urls_to_download):
        output_template = os.path.join(refs_dir, f"ref_video_{i+1}.%(ext)s")
        print(f"[{i+1}/{len(urls_to_download)}] Downloading {url}")
        
        try:
            # yt-dlp to download best quality mp4
            yt_dlp_path = os.path.join(base_dir, "venv", "bin", "yt-dlp")
            subprocess.run(
                [
                    yt_dlp_path,
                    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "-o", output_template,
                    url
                ],
                check=True
            )
            print(f"  -> Downloaded reference {i+1}")
        except subprocess.CalledProcessError as e:
            print(f"  -> Failed to download reference {i+1}: {e}")

if __name__ == "__main__":
    fetch_references()
