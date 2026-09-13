"""Real publishing to YouTube Shorts and Instagram Reels.

Both functions in this module used to print a message and `return True`
without making a single network call, while the orchestrator reported
"published automatically". Nothing in outputs/ was ever uploaded.

Now: a return value means the upload happened and carries the platform's
own ID. Anything else raises PublishError.
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_errors import ConfigError, PublishError  # noqa: E402

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_VERSION = "v21.0"
GRAPH_ROOT = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD_ROOT = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
IG_POLL_ATTEMPTS = 40
IG_POLL_SECONDS = 15


# ------------------------------------------------------------------ metadata

def build_metadata(script_data):
    """Title, description and tags from the script. No platform calls."""
    title = str(
        script_data.get("title") or script_data.get("cover_text") or "Daily Geography"
    ).strip()
    caption = str(script_data.get("caption") or "").strip()

    hashtags = [w for w in caption.split() if w.startswith("#")]
    body = " ".join(w for w in caption.split() if not w.startswith("#")).strip()
    if not hashtags:
        hashtags = ["#Shorts", "#Geography", "#IndiaGK"]
    if "#Shorts" not in hashtags:
        hashtags.insert(0, "#Shorts")

    yt_title = title if title.lower().endswith("shorts") else f"{title} #Shorts"
    return {
        "youtube_title": yt_title[:100],
        "description": (f"{body}\n\n{' '.join(hashtags)}").strip()[:4900],
        "tags": [h.lstrip("#") for h in hashtags][:15],
        "instagram_caption": (f"{body}\n\n{' '.join(hashtags)}").strip()[:2190],
    }


# ------------------------------------------------------------------- youtube

def upload_to_youtube(video_path, script_data):
    """Resumable upload via the YouTube Data API. Returns the video ID."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        raise ConfigError(
            "YouTube upload needs google-api-python-client and google-auth-oauthlib:\n"
            "  ./venv/bin/pip install -r requirements.txt"
        ) from e

    secrets = os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
    if not os.path.isabs(secrets):
        secrets = os.path.join(BASE_DIR, secrets)
    token_file = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
    if not os.path.isabs(token_file):
        token_file = os.path.join(BASE_DIR, token_file)

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, YOUTUBE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not os.path.exists(secrets):
            raise ConfigError(
                f"No YouTube credentials. Put your OAuth client JSON at {secrets}\n"
                f"(Google Cloud console -> APIs & Services -> Credentials ->\n"
                f" OAuth client ID -> Desktop app), then run this once from your\n"
                f"own terminal to authorise:\n"
                f"  ./venv/bin/python -c \"from core.uploader import authorise_youtube; authorise_youtube()\"\n"
                f"The saved token at {token_file} is what lets cron publish unattended."
            )
        if not sys.stdin.isatty():
            raise ConfigError(
                f"YouTube token at {token_file} is missing or expired, and this is a "
                f"non-interactive run (cron). Re-authorise from your own terminal:\n"
                f"  ./venv/bin/python -c \"from core.uploader import authorise_youtube; authorise_youtube()\""
            )
        creds = InstalledAppFlow.from_client_secrets_file(secrets, YOUTUBE_SCOPES).run_local_server(port=0)
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"[Uploader] Saved YouTube token -> {token_file}")

    meta = build_metadata(script_data)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": meta["youtube_title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": os.getenv("YOUTUBE_PRIVACY", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }

    print(f"[Uploader] YouTube: uploading {os.path.basename(video_path)}...")
    media = MediaFileUpload(video_path, chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
        except Exception as e:
            raise PublishError(f"YouTube upload failed: {type(e).__name__}: {e}") from e
        if status:
            print(f"[Uploader]   {int(status.progress() * 100)}%")

    video_id = response.get("id")
    if not video_id:
        raise PublishError(f"YouTube accepted the upload but returned no id: {response!r}")
    print(f"[Uploader] YouTube live: https://youtube.com/shorts/{video_id}")
    return video_id


def authorise_youtube():
    """One-time interactive OAuth. Run from your own terminal."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
    if not os.path.isabs(secrets):
        secrets = os.path.join(BASE_DIR, secrets)
    token_file = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
    if not os.path.isabs(token_file):
        token_file = os.path.join(BASE_DIR, token_file)

    if not os.path.exists(secrets):
        raise ConfigError(f"OAuth client JSON not found at {secrets}")
    creds = InstalledAppFlow.from_client_secrets_file(secrets, YOUTUBE_SCOPES).run_local_server(port=0)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"Authorised. Token saved to {token_file} — cron can now publish unattended.")
    return token_file


# ----------------------------------------------------------------- instagram

def _graph_error(resp):
    try:
        return resp.json().get("error", resp.text)
    except ValueError:
        return resp.text[:400]


def upload_to_instagram(video_path, script_data):
    """Reels via the resumable upload protocol. Returns the published media ID.

    Three steps, all of which used to be a print statement:
      1. create a REELS container with upload_type=resumable
      2. PUT the local file to rupload.facebook.com
      3. poll status_code until FINISHED, then publish
    """
    ig_user_id = os.getenv("IG_USER_ID")
    access_token = os.getenv("IG_ACCESS_TOKEN")
    if not ig_user_id or not access_token:
        raise ConfigError("IG_USER_ID and IG_ACCESS_TOKEN must be set in .env to publish Reels")

    meta = build_metadata(script_data)
    size = os.path.getsize(video_path)

    print(f"[Uploader] Instagram: creating REELS container ({size / 1e6:.1f} MB)...")
    resp = requests.post(
        f"{GRAPH_ROOT}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": meta["instagram_caption"],
            "share_to_feed": "true",
            "access_token": access_token,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise PublishError(f"Container creation failed ({resp.status_code}): {_graph_error(resp)}")
    container_id = resp.json().get("id")
    if not container_id:
        raise PublishError(f"No container id returned: {resp.text[:300]}")

    print(f"[Uploader] Instagram: uploading bytes to container {container_id}...")
    with open(video_path, "rb") as fh:
        up = requests.post(
            f"{RUPLOAD_ROOT}/{container_id}",
            headers={
                "Authorization": f"OAuth {access_token}",
                "offset": "0",
                "file_size": str(size),
                "Content-Type": "application/octet-stream",
            },
            data=fh,
            timeout=900,
        )
    if up.status_code != 200 or not up.json().get("success", True):
        raise PublishError(f"Byte upload failed ({up.status_code}): {up.text[:400]}")

    print("[Uploader] Instagram: waiting for transcode...")
    for attempt in range(1, IG_POLL_ATTEMPTS + 1):
        st = requests.get(
            f"{GRAPH_ROOT}/{container_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=60,
        )
        if st.status_code != 200:
            raise PublishError(f"Status poll failed ({st.status_code}): {_graph_error(st)}")
        code = st.json().get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise PublishError(f"Instagram transcode failed: {st.json().get('status')}")
        print(f"[Uploader]   {code} ({attempt}/{IG_POLL_ATTEMPTS})")
        time.sleep(IG_POLL_SECONDS)
    else:
        raise PublishError(
            f"Container {container_id} never reached FINISHED after "
            f"{IG_POLL_ATTEMPTS * IG_POLL_SECONDS}s"
        )

    pub = requests.post(
        f"{GRAPH_ROOT}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=120,
    )
    if pub.status_code != 200:
        raise PublishError(f"Publish failed ({pub.status_code}): {_graph_error(pub)}")
    media_id = pub.json().get("id")
    if not media_id:
        raise PublishError(f"Publish returned no media id: {pub.text[:300]}")

    print(f"[Uploader] Instagram Reel live: media id {media_id}")
    return media_id


# ---------------------------------------------------------------- orchestrate

def publish_video(video_path, script_data):
    """Publish to both platforms. Reports exactly what happened on each.

    Never raises for a single platform failure — a Reel that went live
    should not be rolled back because YouTube's token expired — but the
    returned dict tells the caller the truth, and the caller decides.
    """
    if not os.path.exists(video_path):
        raise PublishError(f"No video at {video_path}")

    results = {}
    for platform, fn in (("youtube", upload_to_youtube), ("instagram", upload_to_instagram)):
        try:
            results[platform] = {"published": True, "id": fn(video_path, script_data), "error": None}
        except Exception as e:
            results[platform] = {"published": False, "id": None, "error": f"{type(e).__name__}: {e}"}
            print(f"[Uploader] {platform.upper()} FAILED — {type(e).__name__}: {e}")

    results["any_published"] = any(
        v["published"] for k, v in results.items() if isinstance(v, dict)
    )
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(build_metadata({
        "title": "GODAVARI",
        "caption": "The Dakshina Ganga rises at Trimbakeshwar. It crosses five states "
                   "before reaching the sea. #DailyGeo #Geography #IndiaGK #Shorts #UPSC",
    }), indent=2))
