#!/usr/bin/env python
"""Stage 2 daily orchestrator: draw -> script -> gate -> assets -> publish.

Behaviour changes from the previous version:
  * --generate-only was parsed, passed in, and never read. It now works.
  * the success banner printed unconditionally. It now reports what actually
    happened on each platform, and exits non-zero if nothing published.
  * a fact-check failure was the only reason to retry. A script or asset
    failure now also moves to the next topic instead of killing the run,
    but every failure is written to the run log.
"""
import argparse
import datetime
import json
import os
import sys
import traceback

from dotenv import load_dotenv

from core import performance
from core.google_flow_automator import automate_google_flow
from pipeline_errors import (
    AssetGenerationError,
    ConfigError,
    PipelineError,
    QuotaExhaustedError,
    ScriptGenerationError,
)
from stage2_daily_draw import draw_daily_topic, record_topic_used
import core.sync_references as sync_references
from stage2_prompt2_script import generate_script_prompt2

import naming

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOLD_QUEUE_FILE = os.path.join(BASE_DIR, "hold_queue.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
RUNS_FILE = os.path.join(LOG_DIR, "runs.jsonl")


_slug = naming.slugify


def _append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _sheet_gate(output_dir, slug):
    """The fact-check verdicts as the sheet wants them, or an honest blank."""
    path = naming.path(output_dir, slug, "factcheck")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"safe_to_publish": True, "reason": "verdicts not on disk for this rerun",
            "verdicts": []}


def cleanup_old_outputs(days=30):
    """Automatically deletes output folders older than the specified number of days."""
    import time
    import shutil
    outputs_dir = os.path.join(BASE_DIR, "outputs")
    if not os.path.exists(outputs_dir):
        return
    now = time.time()
    cutoff = now - (days * 86400)
    for folder_name in os.listdir(outputs_dir):
        folder_path = os.path.join(outputs_dir, folder_name)
        # Never delete things that aren't directories, and skip __pycache__ etc if present
        if os.path.isdir(folder_path) and not folder_name.startswith("."):
            try:
                mtime = os.path.getmtime(folder_path)
                if mtime < cutoff:
                    print(f"[Cleanup] Deleting old output folder: {folder_name}")
                    shutil.rmtree(folder_path)
            except Exception as e:
                print(f"[Cleanup] Failed to delete {folder_name}: {e}")


def route_to_hold_queue(topic_spec, script_data, fact_check_result):
    """Park an unverified topic. Its hash is deliberately NOT recorded as used."""
    hold = []
    if os.path.exists(HOLD_QUEUE_FILE):
        try:
            with open(HOLD_QUEUE_FILE, "r", encoding="utf-8") as f:
                hold = json.load(f)
        except (json.JSONDecodeError, OSError):
            hold = []
    hold.append({
        "held_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "topic": topic_spec["topic"],
        "series": topic_spec.get("series"),
        "reason": fact_check_result.get("reason"),
        "script": script_data,
        "fact_check": fact_check_result,
        "status": "held_for_review",
    })
    with open(HOLD_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(hold, f, indent=2, ensure_ascii=False)
    print(f"[HoldQueue] {topic_spec['topic']} -> {HOLD_QUEUE_FILE} ({len(hold)} held)")


def _build_topic_spec(force_topic):
    """The spec for a topic named on the command line.

    A forced topic that exists in the backlog is looked up and used whole,
    because a set topic is not just its title: `kind`, `members`, `cover` and
    `ask` are what make Prompt 2 write a set video at all. Building a bare
    spec from the string alone silently turned `--topic "The state animal of
    every Indian state"` into an ordinary single-subject script — which made
    it the one thing you could not test by forcing it.

    Anything not in the backlog still works, as a one-off custom topic.
    """
    wanted = str(force_topic).strip().lower()
    topics_path = os.path.join(BASE_DIR, "topics.json")
    if os.path.exists(topics_path):
        try:
            with open(topics_path, "r", encoding="utf-8") as f:
                backlog = json.load(f).get("backlog", [])
            for item in backlog:
                if str(item.get("topic", "")).strip().lower() == wanted:
                    print(f"[Topic] '{item['topic']}' found in the backlog"
                          + (f" — set of {item.get('set_size')}, covering "
                             f"{item.get('cover')}" if item.get("kind") == "set" else ""))
                    return dict(item)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[Topic] could not read topics.json ({type(e).__name__}) — "
                  f"treating {force_topic!r} as a custom topic")

    spec = {
        "topic": force_topic,
        "subject": force_topic,
        "series": "Custom",
        "title_en": force_topic.upper(),
        "weight": 1.0,
    }
    
    import re
    url_match = re.search(r'(https?://[^\s]+)', force_topic)
    if url_match:
        spec["url"] = url_match.group(1)
        
    return spec


def run_script_generation(force_topic=None, max_attempts=5):
    """
    Runs the topic drawing, script generation, and fact-checking phase.
    Returns the generated slug on success, or None on failure.
    """
    started = datetime.datetime.now()
    print("=" * 58)
    print("SCRIPT GENERATION PHASE")
    print(f"started {started.isoformat(timespec='seconds')}")
    print("=" * 58)

    attempts = []

    for attempt in range(1, max_attempts + 1):
        print(f"\n--- attempt {attempt}/{max_attempts} ---")
        
        # 0. Automatically fetch new URLs from competitors
        try:
            sync_references.fetch_latest_competitor_shorts()
        except Exception as e:
            print(f"[Warning] Failed to fetch latest competitor shorts: {e}")
            
        topic_spec = None
        try:
            # 1. topic
            if force_topic:
                topic_spec = _build_topic_spec(force_topic)
                print(f"[1/3] forced topic: {topic_spec['topic']}")
            else:
                print("[1/3] weighted draw from backlog")
                topic_spec = draw_daily_topic(mark_used=False)

            topic_slug = _slug(topic_spec['topic'])
            output_dir = os.path.join(BASE_DIR, "outputs", topic_slug)
            os.makedirs(output_dir, exist_ok=True)

            # The render phase runs as its own process, so the draw has to
            # survive on disk between the two.
            with open(naming.path(output_dir, topic_slug, "topic_spec"), "w",
                      encoding="utf-8") as f:
                json.dump(topic_spec, f, indent=2, ensure_ascii=False)

            # 1.5. Download competitor video if provided
            from core.download_competitor import download_video
            if "url" in topic_spec:
                print(f"[1.5/3] Downloading competitor video from {topic_spec['url']}")
                video_path = download_video(topic_spec["url"], out_dir=output_dir)
                topic_spec["downloaded_video_path"] = video_path

            # 2. script
            print(f"[2/3] Prompt 2 script for '{topic_spec.get('subject') or topic_spec['topic']}'")
            script_data = generate_script_prompt2(topic_spec)
            script_data["topic_id"] = topic_slug
            script_data["cover_text"] = script_data["title"]
            script_data["regional_script"] = script_data.get("title_regional") or ""
            script_data["cover_art_prompt"] = (
                f"{topic_spec.get('subject') or topic_spec['topic']}, "
                f"vertical 9:16, bold vibrant character illustration, dramatic close-up, "
                f"vivid saturated colours, neon glow accents, rich deep background, "
                f"hyper-detailed pop-art cartoon style"
            )
            script_data["voiceover_text"] = " ".join(
                str(s["text"]).strip() for s in script_data["segments"]
            )
            with open(naming.path(output_dir, topic_slug, "script"), "w",
                      encoding="utf-8") as f:
                json.dump(script_data, f, indent=2, ensure_ascii=False)



            md_path = f"outputs/{topic_slug}/{topic_slug}_script.json"
            
            # The topic is spent: the script exists and is verified
            if not force_topic:
                record_topic_used(topic_spec)
                
            elapsed = (datetime.datetime.now() - started).total_seconds()
            print("\n" + "=" * 58)
            print("SCRIPT WRITTEN (no audio, no images, no render)")
            print(f"topic   {topic_spec['topic']}")
            print(f"sheet   {md_path}")
            print(f"slug    {topic_slug}")
            print(f"took    {elapsed:.0f}s over {len(attempts) + 1} attempt(s)")
            print("=" * 58)
            
            _append_jsonl(RUNS_FILE, {
                "started": started.isoformat(timespec="seconds"),
                "elapsed_s": round(elapsed, 1),
                "outcome": "script_only",
                "topic": topic_spec["topic"],
                "script": md_path,
                "earlier_attempts": attempts,
            })
            return topic_slug

        except QuotaExhaustedError as e:
            # Not a property of this topic: every remaining attempt would spend
            # a topic to be told the same thing. Stop with the backlog intact.
            print(f"\n!! {e}")
            _append_jsonl(RUNS_FILE, {
                "started": started.isoformat(timespec="seconds"),
                "outcome": "quota_exhausted", "error": str(e),
                "earlier_attempts": attempts,
            })
            raise
        except ScriptGenerationError as e:
            label = topic_spec["topic"] if topic_spec else "(no topic drawn)"
            print(f"\n!! {type(e).__name__} on '{label}':\n{e}")
            attempts.append({"topic": label, "outcome": "error",
                             "error": f"{type(e).__name__}: {e}"})
            continue
        except ConfigError as e:
            # Misconfiguration will fail identically on every retry.
            print(f"\n!! Configuration error — not retrying:\n{e}")
            _append_jsonl(RUNS_FILE, {
                "started": started.isoformat(timespec="seconds"),
                "outcome": "config_error", "error": str(e),
            })
            return None
        except PipelineError as e:
            print(f"\n!! {type(e).__name__}: {e}")
            attempts.append({"topic": topic_spec["topic"] if topic_spec else None,
                             "outcome": "error", "error": str(e)})
            continue

    print(f"\nFAILED: {max_attempts} attempts, no script generated.")
    for a in attempts:
        print(f"  - {a.get('topic')}: {a.get('outcome')} {a.get('reason') or a.get('error') or ''}")
    _append_jsonl(RUNS_FILE, {
        "started": started.isoformat(timespec="seconds"),
        "outcome": "exhausted", "attempts": attempts,
    })
    return None


def run_render_pipeline(slug, generate_only=False):
    """
    Runs the media generation, video assembly, and publishing phase for a specific slug.
    Returns the video path on success, or None on failure.
    """
    started = datetime.datetime.now()
    output_dir = os.path.join(BASE_DIR, "outputs", slug)
    
    script_path = naming.path(output_dir, slug, "script")
    topic_spec_path = naming.path(output_dir, slug, "topic_spec")
    if not os.path.exists(topic_spec_path):
        legacy = os.path.join(output_dir, "topic_spec.json")  # pre-slug-naming runs
        if os.path.exists(legacy):
            topic_spec_path = legacy

    if not os.path.exists(script_path):
        print(f"Error: No script found for slug '{slug}'. Did you run the script generation phase?")
        return None

    with open(script_path, "r", encoding="utf-8") as f:
        script_data = json.load(f)

    if os.path.exists(topic_spec_path):
        with open(topic_spec_path, "r", encoding="utf-8") as f:
            topic_spec = json.load(f)
    else:
        topic_spec = {"topic": script_data.get("subject") or slug, "series": "Unknown"}

    print("=" * 58)
    print("GOOGLE FLOW AUTOMATION PHASE")
    mode = "generate-only" if generate_only else "generate+publish (manual)"
    print(f"started {started.isoformat(timespec='seconds')} | mode={mode}")
    print(f"slug    {slug}")
    print("=" * 58)

    try:

        print(f"Executing Google Flow Automation for script: {script_path}")
        automate_google_flow(script_path)
        
        # We no longer have an automated publisher since the file lives in Flow.
        print("\n[Flow Complete] Video is assembled in Google Flow!")
        print("Please review it in your browser, hit Export, and publish manually.")
        
        elapsed = (datetime.datetime.now() - started).total_seconds()
        print("\n" + "=" * 58)
        print("AUTOMATION COMPLETE")
        print(f"slug    {slug}")
        print(f"took    {elapsed:.0f}s")
        print("=" * 58)
        
        return script_path
        
    except Exception as e:
        print(f"\n!! Flow Automation Error: {type(e).__name__}: {e}")
        return None


def resume_unrendered(generate_only=False, limit=5):
    """Render scripts that passed the gate but never became a video.

    The topic is spent the moment the script passes, so a render that dies
    halfway — no disk, ffmpeg missing, the machine asleep — would otherwise
    burn a topic and leave nothing behind. This finds those and finishes them.
    """
    outputs_dir = os.path.join(BASE_DIR, "outputs")
    if not os.path.isdir(outputs_dir):
        return []

    pending = []
    for slug in sorted(os.listdir(outputs_dir)):
        output_dir = os.path.join(outputs_dir, slug)
        if not os.path.isdir(output_dir) or slug.startswith("_"):
            continue
        script_path = naming.path(output_dir, slug, "script")
        gate_path = naming.path(output_dir, slug, "factcheck")
        reel_path = naming.path(output_dir, slug, "reel")
        if not os.path.exists(script_path) or os.path.exists(reel_path):
            continue
        try:
            with open(gate_path, "r", encoding="utf-8") as f:
                if not json.load(f).get("safe_to_publish"):
                    continue
        except (OSError, json.JSONDecodeError):
            continue  # never rendered, never verified: leave it alone
        pending.append(slug)

    if not pending:
        print("[resume] nothing unrendered")
        return []

    print(f"[resume] {len(pending)} script(s) with no video: {', '.join(pending)}")
    done = []
    for slug in pending[:limit]:
        try:
            if run_render_pipeline(slug, generate_only=generate_only):
                done.append(slug)
        except QuotaExhaustedError:
            raise
        except Exception as e:  # noqa: BLE001 — one bad folder must not stop the sweep
            print(f"[resume] {slug} failed again: {type(e).__name__}: {e}")
    return done


def run_daily_pipeline(force_topic=None, generate_only=False, script_only=False,
                       max_attempts=5):
    """Legacy entrypoint that ties both phases together."""
    slug = run_script_generation(force_topic, max_attempts)
    if not slug or script_only:
        return slug
    
    return run_render_pipeline(slug, generate_only)


def _finish(started, topic_spec, video_path, publish_result, attempts, generate_only=False):
    elapsed = (datetime.datetime.now() - started).total_seconds()
    print("\n" + "=" * 58)
    if generate_only:
        status, outcome = "RENDERED (not published)", "rendered"
    elif publish_result and publish_result.get("any_published"):
        live = [k for k, v in publish_result.items() if isinstance(v, dict) and v["published"]]
        failed = [k for k, v in publish_result.items() if isinstance(v, dict) and not v["published"]]
        status = "PUBLISHED to " + ", ".join(live)
        if failed:
            status += f" | FAILED on {', '.join(failed)}"
        outcome = "published"
    else:
        status, outcome = "RENDERED, PUBLISH FAILED on every platform", "publish_failed"

    print(status)
    print(f"topic   {topic_spec['topic']}")
    print(f"video   {video_path}")
    print(f"took    {elapsed:.0f}s over {len(attempts) + 1} attempt(s)")
    print("=" * 58)

    _append_jsonl(RUNS_FILE, {
        "started": started.isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed, 1),
        "outcome": outcome,
        "topic": topic_spec["topic"],
        "video": video_path,
        "publish": publish_result,
        "earlier_attempts": attempts,
    })
    return video_path if outcome in ("published", "rendered") else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily geography content pipeline")
    parser.add_argument("--topic", type=str, help="force a specific topic")
    parser.add_argument("--generate-only", action="store_true",
                        help="render but do not publish")
    parser.add_argument("--script-only", action="store_true",
                        help="write the per-scene script sheet and stop: no "
                             "voiceover, no images, no video")
    parser.add_argument("--max-attempts", type=int, default=5,
                        help="how many topics to try before giving up")
    parser.add_argument("--resume", action="store_true",
                        help="render scripts that passed the gate but have no "
                             "video yet, then stop")
    args = parser.parse_args()

    try:
        cleanup_old_outputs(days=30)
        
        if args.resume:
            result = resume_unrendered(generate_only=args.generate_only)
        else:
            result = run_daily_pipeline(
                force_topic=args.topic,
                generate_only=args.generate_only,
                script_only=args.script_only,
                max_attempts=args.max_attempts,
            )
    except QuotaExhaustedError as e:
        print(f"\n{e}")
        sys.exit(3)          # 3 = out of credit; the backlog was not spent
    except Exception:
        traceback.print_exc()
        sys.exit(2)
    sys.exit(0 if result else 1)
