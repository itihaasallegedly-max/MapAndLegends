#!/usr/bin/env python3
"""DailyGeoMap: generate every scene in Google Flow and DOWNLOAD each clip,
so the pipeline can stitch locally (core/flow_stitch.py) and upload by itself.

Ported from ItihaasaAllegedly/flow_gen.py (which was itself built from the old
core/google_flow_automator.py). Same Chrome-over-CDP approach and same logged-in
profile, so one sign-in (./login_to_flow.sh) serves both channels. Differences
from the old automator:
  * every clip is queued first, then all are waited on together;
  * completion is detected by polling each tile, not a fixed 60 s sleep;
  * each finished clip is downloaded to disk instead of assembled on Flow's timeline.

    ./venv/bin/python core/flow_gen.py job.json

job.json:
    {"aspect": "9:16", "clips": [{"prompt": "...", "seconds": 8, "out": "/abs/path/01.mp4"}, ...]}

Clips whose "out" already exists are skipped, so a crashed run resumes where it
stopped and never pays Flow credits twice for the same scene.
Exit 0 = every clip on disk, 2 = Chrome / login problem, 1 = some clip failed.

Env:
    FLOW_CDP        http://localhost:9222
    FLOW_PROFILE    ~/.dailygeomap_chrome_profile
    FLOW_MODEL      Omni 1.1 Flash
    FLOW_QUALITY    720p
    FLOW_TIMEOUT    600   seconds to wait for one clip
"""
import os, sys, json, time, base64, subprocess, urllib.request
from pathlib import Path

CDP = os.environ.get("FLOW_CDP", "http://localhost:9222")
PROFILE = os.path.expanduser(os.environ.get("FLOW_PROFILE", "~/.dailygeomap_chrome_profile"))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MODEL = os.environ.get("FLOW_MODEL", "Omni 1.1 Flash")
QUALITY = os.environ.get("FLOW_QUALITY", "720p")
TIMEOUT = int(os.environ.get("FLOW_TIMEOUT", "600"))
URL = "https://flow.google.com"
HERE = Path(__file__).resolve().parent.parent   # project root
DEBUG = HERE / "logs"


def log(*a):
    print("[flow]", *a, flush=True)


def cdp_up():
    try:
        urllib.request.urlopen(CDP + "/json/version", timeout=3).read()
        return True
    except Exception:
        return False


def ensure_chrome():
    """Attach to the automation Chrome; start it (same profile as DailyGeoMap) if it isn't running."""
    if cdp_up():
        return True
    port = CDP.rsplit(":", 1)[-1]
    log(f"Chrome not listening on {CDP}; launching it with {PROFILE}")
    subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={port}",
                      "--no-first-run", "--no-default-browser-check", URL],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        time.sleep(1)
        if cdp_up():
            return True
    return False


def dump(page, tag):
    DEBUG.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(DEBUG / f"flow_{tag}_{stamp}.png"))
        (DEBUG / f"flow_{tag}_{stamp}.html").write_text(page.content(), encoding="utf-8")
        log(f"saved logs/flow_{tag}_{stamp}.png/.html for debugging")
    except Exception:
        pass


def pick(page, text):
    try:
        page.get_by_text(text, exact=True).first.click(timeout=1500)
        return True
    except Exception:
        return False


def current_model(page):
    """Model name Flow shows next to the prompt box (e.g. 'Omni 1.1 Flash')."""
    try:
        txt = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None
    if MODEL in txt:
        return MODEL
    import re
    m = re.findall(r"(Omni[ \w.]*?(?:Flash|Pro|Lite|Quality|Fast)|Veo[ \w.]*?(?:Fast|Quality|Lite|Pro)?)\s*$", txt, re.M)
    return m[0].strip() if m else None


def select_model(page):
    """Pick MODEL inside the open settings panel. The model list may sit behind a dropdown."""
    if pick(page, MODEL):
        return True
    overlay = page.locator(".cdk-overlay-container")
    triggers = overlay.locator("[role=combobox], mat-select, [aria-haspopup], button")
    for k in range(min(triggers.count(), 25)):
        t = triggers.nth(k)
        try:
            if not t.is_visible():
                continue
            label = (t.inner_text(timeout=1000) or "").strip()
        except Exception:
            continue
        if not any(w in label for w in ("Omni", "Veo", "Nano", "Flash", "Model", "arrow_drop_down", "expand_more")):
            continue
        try:
            t.click(timeout=2000)
            page.wait_for_timeout(800)
            opt = page.get_by_role("option", name=MODEL).or_(page.get_by_role("menuitem", name=MODEL)) \
                .or_(page.get_by_text(MODEL, exact=True))
            opt.first.click(timeout=2000)
            page.wait_for_timeout(800)
            return True
        except Exception:
            pass
    return False


def set_settings(page, aspect=None, seconds=None):
    try:
        page.locator("button[aria-label='Settings trigger']").first.click(timeout=5000)
        page.wait_for_timeout(1000)
        if aspect:
            for t in ("Video", "Ingredients", aspect):
                if not pick(page, t):
                    log(f"note: setting '{t}' not found")
            if not select_model(page):
                log(f"WARNING: could not select model '{MODEL}'")
                try:   # keep a copy of the settings panel so the selector can be fixed
                    DEBUG.mkdir(exist_ok=True)
                    (DEBUG / "flow_settings_panel.html").write_text(
                        page.locator(".cdk-overlay-container").inner_html(), encoding="utf-8")
                    page.screenshot(path=str(DEBUG / "flow_settings_panel.png"))
                except Exception:
                    pass
            for t in (QUALITY, "x1"):
                pick(page, t)
            try:   # record what the settings panel looks like on every run (overwritten each time)
                DEBUG.mkdir(exist_ok=True)
                page.screenshot(path=str(DEBUG / "flow_settings_last.png"))
            except Exception:
                pass
        if seconds:
            ov = page.locator(".cdk-overlay-container")
            try:
                ov.get_by_text(seconds, exact=True).first.click(timeout=2000)
            except Exception:
                pick(page, seconds)
            page.wait_for_timeout(500)
        if aspect:
            try:
                panel = page.locator(".cdk-overlay-container").inner_text(timeout=2000)
                log(f"model in use: {MODEL if MODEL in panel else 'NOT ' + MODEL}")
            except Exception:
                pass
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
    except Exception as e:
        log(f"note: could not open settings ({e})")


def open_new_project(page):
    page.goto(URL)
    try:
        page.get_by_text("Create with Google Flow").or_(page.get_by_text("New project")) \
            .first.wait_for(state="visible", timeout=60000)
    except Exception:
        pass
    if "accounts.google.com" in page.url:
        return False
    try:
        page.locator("text=Create with Google Flow").first.click(timeout=5000)
    except Exception:
        pass
    try:
        page.locator("text=New project").first.click(timeout=20000)
    except Exception:
        if "accounts.google.com" in page.url:
            return False
    editor = page.locator(".ProseMirror").first
    editor.wait_for(state="visible", timeout=30000)
    return True


def duration_option(sec):
    for opt in (4, 6, 8):
        if sec <= opt:
            return f"{opt}s"
    return "10s"


def video_src(tile):
    v = tile.locator("video")
    if v.count() == 0:
        return None
    try:
        return v.first.evaluate("el => el.currentSrc || el.src || (el.querySelector('source')||{}).src || ''") or None
    except Exception:
        return None


def fix_clip(out):
    """Unzip if Flow handed us a .zip, then make sure it is a real MP4. Bad files are removed."""
    import zipfile
    try:
        if zipfile.is_zipfile(out):
            with zipfile.ZipFile(out) as z:
                mp4s = [n for n in z.namelist() if n.lower().endswith(".mp4")]
                if len(mp4s) != 1:
                    raise ValueError(f"zip has {len(mp4s)} mp4s")
                data = z.read(mp4s[0])
            out.write_bytes(data)
        head = out.read_bytes()[:12]
        if b"ftyp" not in head:
            raise ValueError("not an MP4")
        return True
    except Exception as e:
        log(f"invalid download for {out.name}: {e}")
        try: out.unlink()
        except Exception: pass
        return False


def save_clip(page, context, tile, out):
    """Download the tile's video: direct URL, then in-page blob fetch, then the Download button."""
    try:
        tile.scroll_into_view_if_needed(timeout=5000)
        tile.hover(timeout=5000, force=True)
        page.wait_for_timeout(1500)
    except Exception:
        pass
    src = video_src(tile)
    if src and src.startswith("http"):
        r = context.request.get(src, timeout=120000)
        if r.ok and len(r.body()) > 50_000:
            out.write_bytes(r.body())
            if fix_clip(out):
                return True
    if src and src.startswith("blob:"):
        b64 = tile.locator("video").first.evaluate(
            """async el => { const b = await (await fetch(el.currentSrc || el.src)).blob();
                 return await new Promise(res => { const f = new FileReader();
                   f.onload = () => res(f.result.split(',')[1]); f.readAsDataURL(b); }); }""")
        data = base64.b64decode(b64)
        if len(data) > 50_000:
            out.write_bytes(data)
            if fix_clip(out):
                return True
    # Fallback: Flow's own download controls (tile menu, then panel/batch buttons).
    def _menu_download(click_trigger):
        with page.expect_download(timeout=120000) as dl:
            click_trigger()
            page.wait_for_timeout(1200)
            page.get_by_role("menuitem").filter(has_text="Download").first.click(timeout=4000)
            page.wait_for_timeout(1200)
            for t in (QUALITY, "Original", "Download"):   # quality sub-menu, if one appears
                try:
                    page.get_by_role("menuitem").filter(has_text=t).last.click(timeout=1500)
                    break
                except Exception:
                    pass
        dl.value.save_as(str(out))
        return out.exists() and out.stat().st_size > 50_000

    def _button_download(sel):
        with page.expect_download(timeout=120000) as dl:
            page.locator(sel).first.click(timeout=5000)
            page.wait_for_timeout(1200)
            for t in (QUALITY, "Original"):
                try:
                    page.get_by_role("menuitem").filter(has_text=t).last.click(timeout=1500)
                    break
                except Exception:
                    pass
        dl.value.save_as(str(out))
        return out.exists() and out.stat().st_size > 50_000

    attempts = [
        # Only per-tile downloads: page-level "Download batch" grabs whatever is selected (wrong clip).
        ("tile menu", lambda: _menu_download(lambda: (tile.scroll_into_view_if_needed(timeout=5000),
            tile.hover(timeout=5000, force=True),
            tile.locator("[aria-label='More options']").first.click(timeout=4000, force=True)))),
    ]
    for name, fn in attempts:
        try:
            if fn() and fix_clip(out):
                log(f"downloaded via {name}")
                return True
        except Exception as e:
            log(f"download via {name} failed: {str(e).splitlines()[0][:120]}")
            try: page.keyboard.press("Escape")
            except Exception: pass
    dump(page, "download_fail")
    return False


TILE_SEL = "flow-video-tile, flow-error-tile"
CREDITS_SEL = "[aria-label='Insufficient credits warning']"


class OutOfCredits(Exception):
    """Flow replaced the generate button with its insufficient-credits warning."""


def out_of_credits(page):
    try:
        return page.locator(CREDITS_SEL).count() > 0
    except Exception:
        return False


def tile_state(tile):
    """'done' | 'failed' | 'running' for one grid tile."""
    try:
        if tile.locator("img[alt='Generated video thumbnail'], img.thumbnail").count() > 0 or video_src(tile):
            return "done"
        if tile.evaluate("el => el.tagName.toLowerCase()") == "flow-error-tile":
            return "failed"
        if tile.locator("button[aria-label='Retry']").count() > 0:
            return "failed"
    except Exception:
        pass
    return "running"


def set_duration(page, seconds):
    """Open settings, click the 4s/6s/8s/10s chip, close, and confirm on the prompt-box chip."""
    want = duration_option(seconds)
    chip = ""
    for attempt in range(3):
        set_settings(page, seconds=want)
        try:
            chip = page.locator("button[aria-label='Settings trigger']").first.inner_text(timeout=3000)
        except Exception:
            chip = ""
        if f"· {want}" in chip or f"{want}\n" in chip or chip.rstrip().endswith(want):
            return want
        page.wait_for_timeout(800)
    log(f"note: duration still '{' '.join(chip.split())}', wanted {want}")
    return want


def submit(page, prompt, seconds):
    """Queue one generation and wait until its tile appears (Flow may cap concurrent jobs)."""
    editor = page.locator(".ProseMirror").first
    tiles = page.locator(TILE_SEL)
    before = tiles.count()
    want = set_duration(page, seconds)
    editor.click()
    page.keyboard.press("Meta+a")
    page.keyboard.press("Backspace")
    editor.fill(prompt)
    page.wait_for_timeout(500)
    for attempt in range(12):                     # up to ~6 min if Flow's queue is full
        if out_of_credits(page):
            raise OutOfCredits("Google Flow says there are not enough credits for this clip")
        page.locator("button[aria-label='Start generation']").first.click(timeout=10000)
        for _ in range(30):
            if tiles.count() > before:
                log(f"queued ({want}): {prompt[:60]}…")
                return True
            time.sleep(1)
        log(f"not accepted yet (queue full?) — retrying in 30s [{attempt+1}/12]")
        time.sleep(30)
    return False


CREDITS_EXHAUSTED = False


def run_batch(page, context, clips):
    """Queue EVERY clip first, then wait for all of them together and download each.
    Grid is newest-first, so in a fresh project clip k sits at index (n-1-k)."""
    queued = []
    global CREDITS_EXHAUSTED
    for k, c in enumerate(clips, 1):
        log(f"--- queue {k}/{len(clips)}: {Path(c['out']).name}")
        try:
            ok = submit(page, c["prompt"], float(c.get("seconds", 8)))
        except OutOfCredits as e:
            CREDITS_EXHAUSTED = True
            log(f"OUT OF FLOW CREDITS at clip {k}/{len(clips)} — {e}. Not queuing the rest; "
                f"still waiting for the {len(queued)} already queued so they are not wasted.")
            dump(page, "out_of_credits")
            break
        except Exception as e:  # one bad submit must not abandon clips already paid for
            log(f"could not queue {Path(c['out']).name}: {str(e).splitlines()[0][:120]}")
            dump(page, "queue_error")
            if out_of_credits(page):
                CREDITS_EXHAUSTED = True
                log("Flow is showing its insufficient-credits warning — stopping the queue.")
                break
            continue
        if ok:
            queued.append(c)
        else:
            log(f"could not queue {Path(c['out']).name}")
    n = len(queued)
    if not n:
        return len(clips)
    tiles = page.locator(TILE_SEL)
    saved, retries = set(), {}
    deadline = time.time() + max(TIMEOUT, 150 * n)
    log(f"all {n} queued — waiting for Flow to finish them together")
    while time.time() < deadline and len(saved) < n:
        time.sleep(15)
        total = tiles.count()
        states = []
        for k, c in enumerate(queued):
            if k in saved:
                states.append("saved"); continue
            idx = total - 1 - k
            if idx < 0:
                states.append("?"); continue
            tile = tiles.nth(idx)
            st = tile_state(tile)
            states.append(st)
            out = Path(c["out"]); out.parent.mkdir(parents=True, exist_ok=True)
            if st == "done":
                page.wait_for_timeout(1500)
                if save_clip(page, context, tile, out):
                    saved.add(k); log(f"saved {out.name}")
                else:
                    log(f"DOWNLOAD FAILED {out.name}")
            elif st == "failed" and retries.get(k, 0) < 2:
                retries[k] = retries.get(k, 0) + 1
                log(f"{out.name} failed in Flow — Retry {retries[k]}/2")
                try:
                    tile.locator("button[aria-label='Retry']").first.evaluate("el => el.click()")
                except Exception:
                    pass
        log("status: " + " ".join(f"{i+1}:{st}" for i, st in enumerate(states)))
    if len(saved) < n:
        dump(page, "batch_incomplete")
    return len(clips) - len(saved)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    clips = [c for c in job["clips"] if not Path(c["out"]).exists()]
    if not clips:
        log("all clips already on disk")
        return 0
    log(f"{len(clips)} clip(s) to generate in one batch, aspect {job.get('aspect', '9:16')}")

    if not ensure_chrome():
        log(f"ERROR: could not reach Chrome at {CDP}. Run login_to_flow.sh once and leave it open.")
        return 2

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        context = browser.contexts[0]
        page = context.new_page()
        try:
            if not open_new_project(page):
                log("ERROR: Flow asked for sign-in. Run login_to_flow.sh, sign in, leave Chrome open.")
                dump(page, "login")
                return 2
        except Exception as e:
            log(f"ERROR opening a Flow project: {e}")
            dump(page, "project")
            return 2
        set_settings(page, aspect=job.get("aspect", "9:16"))
        try:
            failed = run_batch(page, context, clips)
        except Exception as e:
            log(f"batch error: {e}")
            dump(page, "batch_error")
            failed = sum(1 for c in clips if not Path(c["out"]).exists())
        try:
            page.close()
        except Exception:
            pass
    log(f"done: {len(clips) - failed}/{len(clips)} clips saved")
    if failed and CREDITS_EXHAUSTED:
        log("Flow credits ran out. Clips saved so far are kept; top up or wait for the monthly "
            "reset, then run  ./venv/bin/python pipeline_daily.py --resume")
        return 3
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
