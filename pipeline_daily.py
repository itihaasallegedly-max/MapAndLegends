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

from core.image_generator import generate_cover_image
from core.uploader import publish_video
from core.video_assembler import assemble_video
from core.voice_generator import generate_voiceover
from pipeline_errors import (
    AssetGenerationError,
    ConfigError,
    FactCheckError,
    PipelineError,
    ScriptGenerationError,
)
from stage2_daily_draw import draw_daily_topic, record_topic_used
from stage2_prompt2_script import generate_script_prompt2
from stage2_prompt3_factcheck import fact_check_claims

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOLD_QUEUE_FILE = os.path.join(BASE_DIR, "hold_queue.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
RUNS_FILE = os.path.join(LOG_DIR, "runs.jsonl")


def _slug(text):
    keep = [c if c.isalnum() or c in " -_" else "" for c in str(text)]
    return "".join(keep).strip().lower().replace(" ", "_").replace("-", "_")[:80] or "topic"


def _append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
    return {
        "topic": force_topic,
        "subject": force_topic,
        "series": "Custom",
        "title_en": force_topic.upper(),
        "weight": 1.0,
    }


def run_daily_pipeline(force_topic=None, generate_only=False, max_attempts=5):
    started = datetime.datetime.now()
    print("=" * 58)
    print("DAILY GEOGRAPHY PIPELINE")
    print(f"started {started.isoformat(timespec='seconds')} | "
          f"mode={'generate-only' if generate_only else 'generate+publish'}")
    print("=" * 58)

    attempts = []

    for attempt in range(1, max_attempts + 1):
        print(f"\n--- attempt {attempt}/{max_attempts} ---")
        topic_spec = None
        try:
            # 1. topic
            if force_topic and attempt == 1:
                topic_spec = _build_topic_spec(force_topic)
                print(f"[1/6] forced topic: {topic_spec['topic']}")
            else:
                print("[1/6] weighted draw from backlog")
                topic_spec = draw_daily_topic(mark_used=False)

            topic_slug = _slug(topic_spec["topic"])
            output_dir = os.path.join(BASE_DIR, "outputs", topic_slug)
            os.makedirs(output_dir, exist_ok=True)

            # 2. script
            print(f"[2/6] Prompt 2 script for '{topic_spec.get('subject') or topic_spec['topic']}'")
            script_data = generate_script_prompt2(topic_spec)
            script_data["topic_id"] = topic_slug
            script_data["cover_text"] = script_data["title"]
            script_data["regional_script"] = script_data.get("title_regional") or ""
            script_data["cover_art_prompt"] = (
                f"{topic_spec.get('subject') or topic_spec['topic']}, "
                f"vertical 9:16 editorial illustration, cinematic lighting"
            )
            script_data["voiceover_text"] = " ".join(
                str(s["text"]).strip() for s in script_data["segments"]
            )
            with open(os.path.join(output_dir, "script_prompt2.json"), "w", encoding="utf-8") as f:
                json.dump(script_data, f, indent=2, ensure_ascii=False)

            # 3. gate
            print("[3/6] Prompt 3 fact-check gate")
            gate = fact_check_claims(script_data["facts"])
            with open(os.path.join(output_dir, "fact_check.json"), "w", encoding="utf-8") as f:
                json.dump(gate, f, indent=2, ensure_ascii=False)

            if not gate.get("safe_to_publish"):
                print(f"[3/6] HELD: {gate.get('reason')}")
                route_to_hold_queue(topic_spec, script_data, gate)
                attempts.append({"topic": topic_spec["topic"], "outcome": "held",
                                 "reason": gate.get("reason")})
                continue
            print(f"[3/6] PASSED: {gate.get('reason')}")

            # 4-5. assets
            print("[4/6] cover art and slides")
            cover_path = generate_cover_image(script_data, output_dir)

            print("[5/6] voiceover and video")
            voice_path = generate_voiceover(
                script_data["voiceover_text"], os.path.join(output_dir, "voiceover.mp3")
            )
            video_path = assemble_video(cover_path, voice_path, script_data, output_dir)

            # Only now is the topic genuinely spent.
            if not force_topic:
                record_topic_used(topic_spec)

            # 6. publish
            if generate_only:
                print("\n[6/6] --generate-only: skipping publish")
                return _finish(started, topic_spec, video_path, None, attempts, generate_only=True)

            print("\n[6/6] publishing")
            publish_result = publish_video(video_path, script_data)
            with open(os.path.join(output_dir, "publish.json"), "w", encoding="utf-8") as f:
                json.dump(publish_result, f, indent=2, ensure_ascii=False)
            return _finish(started, topic_spec, video_path, publish_result, attempts)

        except (ScriptGenerationError, FactCheckError, AssetGenerationError) as e:
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

    print(f"\nFAILED: {max_attempts} attempts, no publishable topic.")
    for a in attempts:
        print(f"  - {a.get('topic')}: {a.get('outcome')} {a.get('reason') or a.get('error') or ''}")
    _append_jsonl(RUNS_FILE, {
        "started": started.isoformat(timespec="seconds"),
        "outcome": "exhausted", "attempts": attempts,
    })
    return None


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
    parser.add_argument("--max-attempts", type=int, default=5,
                        help="how many topics to try before giving up")
    args = parser.parse_args()

    try:
        result = run_daily_pipeline(
            force_topic=args.topic,
            generate_only=args.generate_only,
            max_attempts=args.max_attempts,
        )
    except Exception:
        traceback.print_exc()
        sys.exit(2)
    sys.exit(0 if result else 1)
