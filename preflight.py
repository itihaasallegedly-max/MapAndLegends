#!/usr/bin/env python
"""Everything that has to be true for tonight's run to publish by itself.

An unattended pipeline fails at 4am, in a log nobody reads, one day after the
thing that broke it. This asks the questions in advance — is the token still
good, is there disk, is the backlog empty, did yesterday's video ever go out —
and answers them in one screen.

    ./venv/bin/python preflight.py            # offline checks only
    ./venv/bin/python preflight.py --online   # also asks Gemini what it serves

Exit codes: 0 ready (warnings allowed), 1 something blocks an unattended run.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
load_dotenv()

OK, WARN, FAIL = "OK", "WARN", "FAIL"
results = []


def check(name, status, detail=""):
    results.append((status, name, detail))
    return status


# ------------------------------------------------------------------ checks

def check_env():
    required = {
        "XAI_API_KEY": "no model calls are possible without it",
        "GROK_TEXT_MODEL": "run diagnose_api.py for an ID your key serves",
        "GROK_IMAGE_MODEL": "cover art falls back to brand backdrops without it",
        "VOICE_NAME": "Kokoro voice, e.g. am_michael",
        "CHANNEL_HANDLE": "burned into every slide footer; the renderer refuses without it",
    }
    missing = [k for k in required if not (os.getenv(k) or "").strip()]
    if missing:
        return check("env", FAIL, "missing: " + ", ".join(
            f"{k} ({required[k]})" for k in missing))
    return check("env", OK, f"handle {os.getenv('CHANNEL_HANDLE')}, "
                            f"text {os.getenv('GROK_TEXT_MODEL')}")


def check_python_deps():
    needed = {
        "openai": "openai",
        "PIL": "pillow",
        "dotenv": "python-dotenv",
        "requests": "requests",
    }
    missing = []
    for mod, pkg in needed.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        return check("python packages", FAIL,
                     "missing " + ", ".join(missing) + " — ./venv/bin/pip install -r requirements.txt")
    return check("python packages", OK, "all importable")


def check_ffmpeg():
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            return check("ffmpeg", FAIL, f"{binary} not on PATH")
    try:
        import tempfile
        from core.video_assembler import pick_subtitle_filter, write_ass
        # A temp dir, not logs/: this probe must not depend on being able to
        # delete files inside the project.
        probe_ass = os.path.join(tempfile.gettempdir(), "dailygeomap_preflight.ass")
        write_ass([(0.0, 1.0, "preflight")], probe_ass)
        chosen = pick_subtitle_filter(probe_ass)
        try:
            os.remove(probe_ass)
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001 — reported, not raised
        return check("caption burn-in", FAIL, f"{type(e).__name__}: {str(e)[:200]}")
    if chosen:
        return check("caption burn-in", OK, chosen.split("=")[0] + " filter (libass)")
    return check("caption burn-in", OK,
                 "Pillow overlays — this ffmpeg has no libass, so cues are drawn "
                 "and composited instead. Captions still ship.")


def check_shaping():
    try:
        from PIL import features
        from core.image_generator import UNICODE_FONT_CANDIDATES, pick_font_for
    except Exception as e:  # noqa: BLE001
        return check("regional titles", WARN, f"{type(e).__name__}: {e}")
    has_font = pick_font_for("नागालैंड", 40, UNICODE_FONT_CANDIDATES) is not None
    if not features.check("raqm"):
        return check("regional titles", WARN,
                     "Pillow has no libraqm, so Devanagari would be drawn in storage "
                     "order — the sub-header is skipped instead. Fix: brew install libraqm "
                     "&& ./venv/bin/pip install --upgrade --force-reinstall pillow")
    if not has_font:
        return check("regional titles", WARN, "no font with Indic glyphs; sub-header skipped")
    return check("regional titles", OK, "shaping and fonts present")


def check_voice():
    try:
        from core.voice_generator import resolve_voice
        voice = resolve_voice()
    except Exception as e:  # noqa: BLE001
        return check("voice", FAIL, f"{type(e).__name__}: {e}")
    return check("voice", OK, f"Kokoro {voice}")


def check_art_fallback():
    backdrops = os.path.join(BASE_DIR, "brand", "backdrops")
    pngs = [f for f in os.listdir(backdrops)] if os.path.isdir(backdrops) else []
    pngs = [f for f in pngs if f.endswith(".png")]
    mode = (os.getenv("ART_FALLBACK") or "brand").strip().lower()
    if mode == "brand" and not pngs:
        return check("art fallback", FAIL,
                     "ART_FALLBACK=brand but brand/backdrops holds no .png")
    return check("art fallback", OK, f"{mode} ({len(pngs)} backdrops)")


def check_audio_bed():
    """A missing bed is never fatal — but it should be visible, not silent."""
    try:
        from core import audio_bed
        present = audio_bed.available()
    except Exception as e:  # noqa: BLE001
        return check("audio bed", WARN, f"{type(e).__name__}: {str(e)[:120]}")
    if not any(present.values()):
        return check("audio bed", WARN,
                     "brand/audio is empty, so reels ship as narration over "
                     "silence. Drop licensed files into brand/audio/music/.")
    laid = ", ".join(kind for kind, ok in present.items() if ok)
    return check("audio bed", OK, f"{laid} present")


def check_feedback_loop():
    """Whether the weekly reweight can ever learn anything."""
    try:
        from core import performance
        state = performance.summary()
    except Exception as e:  # noqa: BLE001
        return check("feedback loop", WARN, f"{type(e).__name__}: {str(e)[:120]}")
    if state["ready"]:
        return check("feedback loop", OK,
                     f"{state['measured']} published reels measured — weights now "
                     f"come from your own channel")
    if state["published"]:
        return check("feedback loop", WARN,
                     f"{state['measured']} of {state['published']} reels measured; "
                     f"{state['needed']} more before your own numbers drive the weights")
    return check("feedback loop", WARN,
                 "nothing published through the pipeline yet, so the weights "
                 "cannot move. They start learning after the first reels go live.")


def check_collections():
    """The backlog's shape, which is what the teardown says decides views."""
    topics_path = os.path.join(BASE_DIR, "topics.json")
    if not os.path.exists(topics_path):
        return check("backlog shape", FAIL, "topics.json missing")
    with open(topics_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    topics = data.get("backlog", [])
    sets = [t for t in topics if t.get("kind") == "set"]
    lift = (data.get("shape_basis") or {}).get("set_lift")
    basis = data.get("weight_basis", "unknown")
    if not sets:
        return check("backlog shape", FAIL,
                     "no set topics — rerun stage0_prompt1_backlog.py; the "
                     "teardown measured collections out-performing single "
                     "subjects by an order of magnitude")
    detail = f"{len(sets)} of {len(topics)} are sets, weights from {basis}"
    if lift:
        detail += f", set lift {lift}x"
    if basis not in ("own_channel", "apify_scrape"):
        return check("backlog shape", WARN,
                     detail + " — someone else's snapshot. Your own published "
                     "reels take over automatically once enough of them are "
                     "measured; see the feedback loop check.")
    return check("backlog shape", OK, detail)


def check_references():
    """Which reference reel Prompt 2 will actually show the model."""
    refs = os.path.join(BASE_DIR, "brand", "references")
    videos = [f for f in os.listdir(refs)
              if f.lower().endswith((".mp4", ".mov", ".m4v"))] if os.path.isdir(refs) else []
    if not videos:
        return check("style references", WARN,
                     "brand/references is empty — Prompt 2 writes shots with no "
                     "style to match")
    if len(videos) == 1:
        return check("style references", WARN,
                     f"one reference ({videos[0][:40]}) — a handful of the reels "
                     f"you want to look like would do more than prompt wording")
    return check("style references", OK, f"{len(videos)} references")



def check_map():
    """The map shot needs bundled geometry and nothing else — no key, no tiles."""
    geo = os.path.join(BASE_DIR, "geo")
    missing = [f for f in ("countries.geojson", "states.geojson", "rivers.geojson")
               if not os.path.exists(os.path.join(geo, f))]
    if missing:
        return check("map layer", FAIL, "geo/ is missing " + ", ".join(missing))
    try:
        from core.map_animator import find_subject
        hit = find_subject("Nagaland")
    except Exception as e:  # noqa: BLE001
        return check("map layer", FAIL, f"{type(e).__name__}: {str(e)[:160]}")
    if not hit:
        return check("map layer", FAIL, "geometry loaded but nothing resolves")
    return check("map layer", OK, "36 Indian states, 177 countries, 1367 rivers")


def check_youtube():
    secrets = os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
    token = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
    secrets = secrets if os.path.isabs(secrets) else os.path.join(BASE_DIR, secrets)
    token = token if os.path.isabs(token) else os.path.join(BASE_DIR, token)

    if not os.path.exists(token):
        return check("youtube", FAIL,
                     f"no token at {os.path.basename(token)} — unattended publishing to "
                     f"YouTube is impossible until you authorise once:\n"
                     f"      ./venv/bin/python -c \"from core.uploader import authorise_youtube; authorise_youtube()\"" +
                     ("" if os.path.exists(secrets) else
                      f"\n      (and put your OAuth desktop client JSON at {os.path.basename(secrets)} first)"))
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from core.uploader import YOUTUBE_SCOPES
        creds = Credentials.from_authorized_user_file(token, YOUTUBE_SCOPES)
        if creds.valid:
            return check("youtube", OK, "token valid")
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            return check("youtube", OK, "token refreshed")
        return check("youtube", FAIL, "token present but not refreshable — re-authorise")
    except Exception as e:  # noqa: BLE001
        return check("youtube", FAIL, f"{type(e).__name__}: {str(e)[:200]}")


def check_instagram(online=False):
    user_id = (os.getenv("IG_USER_ID") or "").strip()
    token = (os.getenv("IG_ACCESS_TOKEN") or "").strip()
    if not user_id or not token:
        return check("instagram", FAIL, "IG_USER_ID or IG_ACCESS_TOKEN missing")
    if not online:
        return check("instagram", OK, f"credentials present for {user_id}")
    try:
        import requests
        r = requests.get(
            "https://graph.facebook.com/v21.0/debug_token",
            params={"input_token": token, "access_token": token}, timeout=20,
        )
        data = r.json().get("data", {})
        expires = data.get("expires_at")
        if not data.get("is_valid"):
            return check("instagram", FAIL, f"token rejected: {data.get('error', r.text[:120])}")
        if expires:
            left = datetime.datetime.fromtimestamp(expires) - datetime.datetime.now()
            days = left.days
            if days < 7:
                return check("instagram", WARN, f"token expires in {days} day(s) — refresh it")
            return check("instagram", OK, f"token valid, {days} days left")
        return check("instagram", OK, "token valid, no expiry (system user)")
    except Exception as e:  # noqa: BLE001
        return check("instagram", WARN, f"could not verify: {type(e).__name__}: {e}")


def check_backlog():
    topics_path = os.path.join(BASE_DIR, "topics.json")
    used_path = os.path.join(BASE_DIR, "used_topics.sha1")
    if not os.path.exists(topics_path):
        return check("backlog", FAIL, "topics.json missing — run stage0_prompt1_backlog.py")
    with open(topics_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    topics = data.get("backlog", [])
    used = set()
    if os.path.exists(used_path):
        with open(used_path, "r", encoding="utf-8") as f:
            used = set(f.read().split())
    # Counted the way the draw counts it: by hash, not by subtracting lines.
    from stage2_daily_draw import slug_hash
    left = len([t for t in topics if slug_hash(t) not in used])
    if left <= 7:
        return check("backlog", FAIL, f"{left} topics left — rebuild before it runs dry")
    if left <= 30:
        return check("backlog", WARN, f"{left} topics left (~{left} days)")
    return check("backlog", OK, f"{left} of {len(topics)} topics unused")


def check_disk():
    usage = shutil.disk_usage(BASE_DIR)
    free_gb = usage.free / 1e9
    # A finished run costs roughly 25-30 MB of video, art and audio.
    if free_gb < 1:
        return check("disk", FAIL, f"{free_gb:.1f} GB free")
    if free_gb < 5:
        return check("disk", WARN, f"{free_gb:.1f} GB free")
    return check("disk", OK, f"{free_gb:.0f} GB free")


def check_heartbeat():
    beat = os.path.join(BASE_DIR, "logs", ".last_daily")
    if not os.path.exists(beat):
        return check("schedule", WARN,
                     "logs/.last_daily missing — the daily job has never run. "
                     "Install it: ./install_schedule.sh")
    age = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(beat))
    hours = age.total_seconds() / 3600
    if hours > 36:
        return check("schedule", FAIL,
                     f"last run {hours:.0f}h ago — the scheduler is not firing")
    if hours > 26:
        return check("schedule", WARN, f"last run {hours:.0f}h ago")
    return check("schedule", OK, f"last run {hours:.0f}h ago")


def check_publish_queue():
    from core import publish_queue
    s = publish_queue.summary()
    if s["abandoned"]:
        return check("publish queue", FAIL,
                     f"{s['abandoned']} reel(s) given up on — see publish_queue.json")
    if s["pending"]:
        return check("publish queue", WARN, f"{s['pending']} waiting to retry")
    return check("publish queue", OK, "empty")


def check_unrendered():
    outputs = os.path.join(BASE_DIR, "outputs")
    if not os.path.isdir(outputs):
        return check("unrendered scripts", OK, "no outputs yet")
    import naming
    stuck = []
    for slug in os.listdir(outputs):
        d = os.path.join(outputs, slug)
        if not os.path.isdir(d) or slug.startswith("_"):
            continue
        if (os.path.exists(naming.path(d, slug, "script"))
                and not os.path.exists(naming.path(d, slug, "reel"))):
            stuck.append(slug)
    if stuck:
        return check("unrendered scripts", WARN,
                     f"{len(stuck)} script(s) with no video — "
                     f"./venv/bin/python pipeline_daily.py --resume")
    return check("unrendered scripts", OK, "none")


def check_models_online():
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("XAI_API_KEY"), base_url="https://api.x.ai/v1")
        served = {m.id for m in client.models.list().data}
    except Exception as e:  # noqa: BLE001
        return check("xai models", FAIL, f"{type(e).__name__}: {str(e)[:200]}")

    from core import model_client
    missing = [m for m in model_client.text_models() + model_client.image_models()
               if m not in served]
    if missing:
        return check("xai models", FAIL,
                     f"not served by this key: {', '.join(missing)} — run diagnose_api.py")
    return check("xai models", OK, f"{len(served)} models served, all configured IDs present")


# -------------------------------------------------------------------- main

def main(online=False):
    check_env()
    check_python_deps()
    check_ffmpeg()
    check_shaping()
    check_voice()
    check_art_fallback()
    check_map()
    check_youtube()
    check_instagram(online=online)
    check_backlog()
    check_collections()
    check_feedback_loop()
    check_audio_bed()
    check_references()
    check_disk()
    check_heartbeat()
    check_publish_queue()
    check_unrendered()
    if online:
        check_models_online()

    width = max(len(name) for _, name, _ in results)
    print("=" * 58)
    print("PREFLIGHT — can this pipeline run unattended tonight?")
    print("=" * 58)
    for status, name, detail in results:
        print(f"[{status:4}] {name.ljust(width)}  {detail}")

    failures = [r for r in results if r[0] == FAIL]
    warnings = [r for r in results if r[0] == WARN]
    print("-" * 58)
    if failures:
        print(f"{len(failures)} blocking, {len(warnings)} warning(s). "
              f"An unattended run would not publish.")
        return 1
    print(f"Ready. {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unattended-readiness check")
    parser.add_argument("--online", action="store_true",
                        help="also verify model IDs and the Instagram token over the network")
    args = parser.parse_args()
    sys.exit(main(online=args.online))
