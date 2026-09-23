"""Real publishing to YouTube Shorts and Instagram Reels.

Both functions in this module used to print a message and `return True`
without making a single network call, while the orchestrator reported
"published automatically". Nothing in outputs/ was ever uploaded.

Now: a return value means the upload happened and carries the platform's
own ID. Anything else raises PublishError.
"""
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_errors import ConfigError, PublishError  # noqa: E402

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_VERSION = "v21.0"
FB_GRAPH_ROOT = f"https://graph.facebook.com/{GRAPH_VERSION}"
IG_GRAPH_ROOT = f"https://graph.instagram.com/{GRAPH_VERSION}"


def graph_root(token=None):
    """Instagram-Login tokens (IGAA...) live on graph.instagram.com; Facebook-Login
    tokens (EAA...) on graph.facebook.com. Same endpoints, different host."""
    token = token if token is not None else os.getenv("IG_ACCESS_TOKEN", "")
    return IG_GRAPH_ROOT if token.strip().startswith("IG") else FB_GRAPH_ROOT


GRAPH_ROOT = FB_GRAPH_ROOT  # kept for old imports; upload_to_instagram uses graph_root()
RUPLOAD_ROOT = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # readonly lets every upload first confirm WHICH channel the token belongs to
    "https://www.googleapis.com/auth/youtube.readonly",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_CMD = './venv/bin/python -c "from core.uploader import authorise_youtube; authorise_youtube()"'
IG_POLL_ATTEMPTS = 40
IG_POLL_SECONDS = 15


# ------------------------------------------------------------------ metadata

def build_metadata(script_data):
    """Title, description and tags from the script. No platform calls.

    Prefers the SEO fields Prompt 2 now writes (upload_title, upload_hashtags);
    falls back to the cover title and the caption's own hashtags.
    """
    title = str(
        script_data.get("upload_title") or script_data.get("title")
        or script_data.get("cover_text") or "Daily Geography"
    ).strip()
    caption = str(script_data.get("caption") or "").strip()

    hashtags = [w for w in caption.split() if w.startswith("#")]
    body = " ".join(w for w in caption.split() if not w.startswith("#")).strip()
    for tag in script_data.get("upload_hashtags") or []:
        tag = "#" + re.sub(r"[^A-Za-z0-9_]", "", str(tag).lstrip("#"))
        if len(tag) > 2 and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)
    if not hashtags:
        hashtags = ["#Geography", "#Maps"]
    if "#shorts" not in {h.lower() for h in hashtags}:
        hashtags.insert(0, "#Shorts")

    yt_title = title if "#shorts" in title.lower() else f"{title} #Shorts"
    return {
        "youtube_title": yt_title[:100],
        "description": (f"{body}\n\n{' '.join(hashtags)}").strip()[:4900],
        "tags": [h.lstrip("#") for h in hashtags][:15],
        "instagram_caption": (f"{body}\n\n{' '.join(hashtags[:30])}").strip()[:2190],
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

    creds = youtube_credentials()
    meta = build_metadata(script_data)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    assert_channel(youtube)
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


def _path_env(name, default):
    p = os.getenv(name, default)
    return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)


def youtube_credentials():
    """Refreshed credentials: YT_REFRESH_TOKEN in .env first, else the token file."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    refresh = os.getenv("YT_REFRESH_TOKEN", "").strip()
    cid, csec = os.getenv("YT_CLIENT_ID", "").strip(), os.getenv("YT_CLIENT_SECRET", "").strip()
    token_file = _path_env("YOUTUBE_TOKEN_FILE", "youtube_token.json")
    if refresh:
        if not (cid and csec):
            raise ConfigError("YT_REFRESH_TOKEN is set but YT_CLIENT_ID / YT_CLIENT_SECRET are not")
        creds = Credentials(None, refresh_token=refresh, token_uri=TOKEN_URI,
                            client_id=cid, client_secret=csec)
    elif os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file)
    else:
        raise ConfigError(f"No YouTube token for this channel. Authorise once from your own terminal:\n  {AUTH_CMD}")
    try:
        creds.refresh(Request())
    except Exception as e:  # invalid_grant = revoked, expired (7 days in Testing mode) or wrong client
        raise ConfigError(f"YouTube token refresh failed ({type(e).__name__}: {e}). "
                          f"Re-authorise:\n  {AUTH_CMD}") from e
    return creds


def token_channel(youtube):
    """(id, title) of the channel this token uploads to."""
    items = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise ConfigError("This YouTube token has no channel attached")
    return items[0]["id"], items[0]["snippet"]["title"]


def assert_channel(youtube):
    """Refuse to upload anywhere but YOUTUBE_CHANNEL_ID. Refresh tokens are
    per-channel; a token copied from another project would post there."""
    want = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()
    if not want:
        return
    try:
        got, title = token_channel(youtube)
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f"Could not confirm which channel this token belongs to ({type(e).__name__}: "
                          f"{str(e)[:160]}). It probably lacks the youtube.readonly scope — "
                          f"re-authorise:\n  {AUTH_CMD}") from e
    if got != want:
        raise ConfigError(f"YouTube token belongs to '{title}' ({got}), not {want}. "
                          f"Nothing uploaded. Re-authorise and pick Map & Legend:\n  {AUTH_CMD}")


def authorise_youtube():
    """One-time interactive OAuth from your own terminal.

    Pick the Map & Legend channel in Google's chooser. The token is only
    saved (as YT_REFRESH_TOKEN in .env) if it really belongs to YOUTUBE_CHANNEL_ID.
    """
    import re as _re
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    cid, csec = os.getenv("YT_CLIENT_ID", "").strip(), os.getenv("YT_CLIENT_SECRET", "").strip()
    secrets = _path_env("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
    if cid and csec:
        flow = InstalledAppFlow.from_client_config(
            {"installed": {"client_id": cid, "client_secret": csec, "token_uri": TOKEN_URI,
                           "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                           "redirect_uris": ["http://localhost"]}}, YOUTUBE_SCOPES)
    elif os.path.exists(secrets):
        flow = InstalledAppFlow.from_client_secrets_file(secrets, YOUTUBE_SCOPES)
    else:
        raise ConfigError("Set YT_CLIENT_ID and YT_CLIENT_SECRET in .env first")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    got, title = token_channel(build("youtube", "v3", credentials=creds, cache_discovery=False))
    want = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()
    print(f"Token is for: {title} ({got})")
    if want and got != want:
        raise ConfigError(f"That is not {want}. Not saved — run again and choose Map & Legend.")

    env_path = os.path.join(BASE_DIR, ".env")
    with open(env_path, encoding="utf-8") as f:
        env = f.read()
    line = f"YT_REFRESH_TOKEN={creds.refresh_token}"
    env = (_re.sub(r"^YT_REFRESH_TOKEN=.*$", line, env, flags=_re.M)
           if _re.search(r"^YT_REFRESH_TOKEN=", env, _re.M) else env.rstrip("\n") + "\n" + line + "\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env)
    print("Saved YT_REFRESH_TOKEN to .env — unattended uploads will go to this channel.")
    return got


# ----------------------------------------------------------------- instagram

def _graph_error(resp):
    try:
        return resp.json().get("error", resp.text)
    except ValueError:
        return resp.text[:400]


def assert_instagram_account(token):
    """Refuse to post unless the token belongs to IG_USERNAME / IG_USER_ID."""
    want_user = os.getenv("IG_USERNAME", "").strip().lstrip("@").lower()
    want_id = os.getenv("IG_USER_ID", "").strip()
    if not (want_user or want_id):
        return
    r = requests.get(f"{graph_root(token)}/me", params={"fields": "user_id,username",
                                                        "access_token": token}, timeout=30)
    if r.status_code != 200:
        raise ConfigError(f"Instagram token rejected ({r.status_code}): {_graph_error(r)}")
    me = r.json()
    ids = {str(me.get("user_id", "")), str(me.get("id", ""))}
    if (want_user and str(me.get("username", "")).lower() != want_user) or (want_id and want_id not in ids):
        raise ConfigError(f"Instagram token is for @{me.get('username')} ({me.get('user_id') or me.get('id')}), "
                          f"not @{want_user} ({want_id}). Nothing posted.")


def refresh_instagram_token(min_age_days=7, force=False):
    """Keep the 60-day Instagram-Login token alive: refresh it (at most weekly)
    and write the new one back to .env. Returns a short status string."""
    import datetime as _dt
    import re as _re
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    if not token.startswith("IG"):
        return "not an Instagram-Login token — nothing to refresh"
    last = os.getenv("IG_TOKEN_REFRESHED", "").strip()
    try:
        age = (_dt.date.today() - _dt.date.fromisoformat(last)).days
    except ValueError:
        age = 999
    if age < min_age_days and not force:
        return f"refreshed {age} day(s) ago — not due"
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    if r.status_code != 200 or "access_token" not in r.json():
        raise ConfigError(f"Instagram token refresh failed ({r.status_code}): {_graph_error(r)}")
    new, days = r.json()["access_token"], int(r.json().get("expires_in", 0)) // 86400
    env_path = os.path.join(BASE_DIR, ".env")
    with open(env_path, encoding="utf-8") as f:
        env = f.read()
    for k, v in (("IG_ACCESS_TOKEN", new), ("IG_TOKEN_REFRESHED", _dt.date.today().isoformat())):
        line = f"{k}={v}"
        env = (_re.sub(rf"^{k}=.*$", lambda _m: line, env, flags=_re.M)
               if _re.search(rf"^{k}=", env, _re.M) else env.rstrip("\n") + "\n" + line + "\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env)
    os.environ["IG_ACCESS_TOKEN"] = new
    return f"refreshed, valid ~{days} days"


def _ig_publish_via_url(video_path, meta, ig_user_id, access_token, root, attempts=4):
    """Instagram-Login path: host the reel publicly, create the container from
    video_url, wait, publish. Each attempt uses a different host, because the
    usual failure is Meta being unable to fetch from one particular host."""
    from pathlib import Path
    from core.public_host import HOSTS, upload_to_public_host
    bad, last = set(), None
    for attempt in range(1, attempts + 1):
        if len(bad) >= len(HOSTS):
            bad.clear()
        host, url = upload_to_public_host(Path(video_path), skip=bad)
        print(f"[Uploader] Instagram attempt {attempt}/{attempts}: video hosted on {host}")
        resp = requests.post(f"{root}/{ig_user_id}/media", data={
            "media_type": "REELS", "video_url": url, "caption": meta["instagram_caption"],
            "share_to_feed": "true", "thumb_offset": "3000", "access_token": access_token,
        }, timeout=60)
        if resp.status_code != 200:
            raise PublishError(f"Container creation failed ({resp.status_code}): {_graph_error(resp)}")
        container_id = resp.json().get("id")
        code, detail = None, ""
        for i in range(1, IG_POLL_ATTEMPTS + 1):
            time.sleep(IG_POLL_SECONDS)
            st = requests.get(f"{root}/{container_id}", params={
                "fields": "status_code,status", "access_token": access_token}, timeout=60).json()
            code, detail = st.get("status_code"), st.get("status", "")
            if code in ("FINISHED", "ERROR"):
                break
            print(f"[Uploader]   {code} ({i}/{IG_POLL_ATTEMPTS})")
        if code == "FINISHED":
            pub = requests.post(f"{root}/{ig_user_id}/media_publish", data={
                "creation_id": container_id, "access_token": access_token}, timeout=120)
            if pub.status_code != 200:
                raise PublishError(f"Publish failed ({pub.status_code}): {_graph_error(pub)}")
            media_id = pub.json().get("id")
            print(f"[Uploader] Instagram Reel live: media id {media_id}")
            return media_id
        last = f"{code}: {detail}"
        print(f"[Uploader] Instagram could not process the video from {host} ({last}) — next host")
        bad.add(host)
    raise PublishError(f"Instagram failed on {attempts} hosts; last status {last}")


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

    root = graph_root(access_token)
    assert_instagram_account(access_token)
    meta = build_metadata(script_data)
    size = os.path.getsize(video_path)
    if root == IG_GRAPH_ROOT:   # Instagram-Login token: no resumable upload, needs a public URL
        return _ig_publish_via_url(video_path, meta, ig_user_id, access_token, root)

    print(f"[Uploader] Instagram: creating REELS container ({size / 1e6:.1f} MB)...")
    resp = requests.post(
        f"{root}/{ig_user_id}/media",
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
            f"{root}/{container_id}",
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
        f"{root}/{ig_user_id}/media_publish",
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

PLATFORMS = (("youtube", "upload_to_youtube"), ("instagram", "upload_to_instagram"))


def publish_video(video_path, script_data, platforms=None):
    """Publish to each platform (or only those named). Reports what happened on each.

    Never raises for a single platform failure — a Reel that went live
    should not be rolled back because YouTube's token expired — but the
    returned dict tells the caller the truth, and the caller decides.
    """
    if not os.path.exists(video_path):
        raise PublishError(f"No video at {video_path}")

    results = {}
    for platform, fn_name in PLATFORMS:
        if platforms is not None and platform not in platforms:
            continue
        try:
            results[platform] = {"published": True,
                                 "id": globals()[fn_name](video_path, script_data), "error": None}
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
