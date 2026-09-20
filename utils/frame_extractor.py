import os
import subprocess
import tempfile
import base64

def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of",
        "default=noprint_wrappers=1:nokey=1", video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting duration: {e}")
        return 0

def extract_frames_base64(video_path, num_frames=3):
    """
    Extracts evenly spaced frames from a video and returns them as a list of base64 encoded strings.
    """
    duration = get_video_duration(video_path)
    if duration == 0:
        return []
    
    frames_b64 = []
    
    # Extract frames at evenly spaced intervals (e.g. 25%, 50%, 75%)
    for i in range(1, num_frames + 1):
        timestamp = (duration / (num_frames + 1)) * i
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
            temp_path = tmp_file.name
            
        cmd = [
            "ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path,
            "-vframes", "1", "-q:v", "2", temp_path
        ]
        
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            with open(temp_path, "rb") as f:
                b64_str = base64.b64encode(f.read()).decode("utf-8")
                frames_b64.append(b64_str)
        except Exception as e:
            print(f"Error extracting frame at {timestamp}s: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    return frames_b64
