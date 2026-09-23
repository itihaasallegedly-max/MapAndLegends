"""Temporary public hosting for a finished reel, so Instagram can fetch it.

Instagram-Login tokens (graph.instagram.com) do not accept the resumable
byte upload that Facebook-Login tokens use ("The parameter video_url is
required"), so the reel has to sit at a public URL for a few minutes while
Meta's fetcher pulls it. Ported from ItihaasaAllegedly/upload_instagram.py,
which has been publishing daily this way; its host notes are kept below.
"""
import requests

# Roster as of 2026-07-14: litterbox is the proven-good host (keep first; its
# 500 outage fails fast). uguu/pixeldrain/envs.sh added fresh. tmpfiles and
# catbox are Meta-blocklisted (upload works, IG fetch ERRORs) — last resorts.
# transfer.sh removed (service shut down, connection refused).
HOSTS = [
    ("litterbox",   lambda p: _upload_litterbox(p)),
    ("uguu.se",     lambda p: _upload_uguu(p)),
    ("pixeldrain",  lambda p: _upload_pixeldrain(p)),
    ("envs.sh",     lambda p: _upload_envs(p)),
    ("tmpfiles.org", lambda p: _upload_tmpfiles(p)),
    ("0x0.st",      lambda p: _upload_0x0(p)),
    ("catbox.moe",  lambda p: _upload_catbox(p)),
]

def upload_to_public_host(file_path, skip=()):
    """Upload file to a public host for Instagram API access. Tries multiple hosts.

    Returns (host_name, url). `skip` = hosts Instagram's fetcher already ERRORed
    on this run; the *upload* succeeding there is useless, so don't reuse them.
    (2026-07-14: litterbox had a 500 outage and Meta started ERROR-ing on
    tmpfiles.org fetches, so all 3 retries burned on the same dead host.
    2026-07-04: Meta stopped fetching files.catbox.moe — catbox last resort.)"""
    for name, fn in HOSTS:
        if name in skip:
            print(f"  – skipping {name} (Instagram couldn't fetch from it this run)")
            continue
        try:
            print(f"Uploading {file_path.name} to {name}...")
            url = fn(file_path)
            if url and url.startswith("http"):
                print(f"  ✓ Public URL obtained from {name}")
                return name, url
        except Exception as e:
            print(f"  ✗ {name} failed: {e} — trying next host...")
    raise RuntimeError("All file hosts failed. Cannot upload to Instagram.")

def _upload_litterbox(file_path):
    """catbox's temporary sibling (1h expiry, served from litter.catbox.moe).
    Instagram fetches this host reliably where files.catbox.moe now fails."""
    with open(file_path, "rb") as f:
        r = requests.post("https://litterbox.catbox.moe/resources/internals/api.php",
                          data={"reqtype": "fileupload", "time": "1h"},
                          files={"fileToUpload": (file_path.name, f)}, timeout=120)
        r.raise_for_status()
        return r.text.strip()

def _upload_tmpfiles(file_path):
    """tmpfiles.org — returns a page URL; the direct file is the /dl/ variant,
    which is what Instagram must fetch."""
    with open(file_path, "rb") as f:
        r = requests.post("https://tmpfiles.org/api/v1/upload",
                          files={"file": (file_path.name, f)}, timeout=120)
        r.raise_for_status()
        url = r.json()["data"]["url"]
        return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")

def _upload_0x0(file_path):
    # 0x0.st rejects the default python-requests User-Agent with 403 — a real
    # UA string is mandatory, which is why this host never worked before.
    with open(file_path, "rb") as f:
        r = requests.post("https://0x0.st", files={"file": (file_path.name, f)},
                          headers={"User-Agent": "DailyGeoMap-pipeline/1.0 (contact: mahi130695@gmail.com)"},
                          timeout=120)
        r.raise_for_status()
        return r.text.strip()

def _upload_uguu(file_path):
    """uguu.se — temp host (3h expiry, 128 MiB max), serves direct file URLs."""
    with open(file_path, "rb") as f:
        r = requests.post("https://uguu.se/upload",
                          files={"files[]": (file_path.name, f)}, timeout=120)
        r.raise_for_status()
        return r.json()["files"][0]["url"]

def _upload_pixeldrain(file_path):
    """pixeldrain.com — anonymous upload; /api/file/<id> serves the raw file
    with a correct content-type, which Meta's fetcher handles well."""
    with open(file_path, "rb") as f:
        r = requests.post("https://pixeldrain.com/api/file",
                          files={"file": (file_path.name, f)}, timeout=120)
        r.raise_for_status()
        return f"https://pixeldrain.com/api/file/{r.json()['id']}"

def _upload_envs(file_path):
    """envs.sh — another instance of the 0x0 software; same UA requirement."""
    with open(file_path, "rb") as f:
        r = requests.post("https://envs.sh", files={"file": (file_path.name, f)},
                          headers={"User-Agent": "DailyGeoMap-pipeline/1.0 (contact: mahi130695@gmail.com)"},
                          timeout=120)
        r.raise_for_status()
        return r.text.strip()

def _upload_catbox(file_path):
    with open(file_path, "rb") as f:
        r = requests.post("https://catbox.moe/user/api.php",
                          data={"reqtype": "fileupload"},
                          files={"fileToUpload": (file_path.name, f)}, timeout=120)
        r.raise_for_status()
        return r.text.strip()


