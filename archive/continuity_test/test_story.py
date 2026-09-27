"""Test the story brief + new Flow prompt on the Cyprus competitor video.

    ./venv/bin/python test_story.py            # step 1: script only (Gemini, no Flow credits)
    ./venv/bin/python test_story.py --render   # step 2: Flow clips + stitch, NOT published

Everything goes to outputs/cyprus_story_test/ — the published reel's folder
and the topic ledger are not touched.
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(BASE), str(BASE / "core")]
os.chdir(BASE)

SLUG = "cyprus_story_test"
OUT = BASE / "outputs" / SLUG
SPEC = {
    "topic": "Why Cyprus Is Divided — Europe's Strangest Island (pf2wPSCLr3o)",
    "url": "https://www.youtube.com/shorts/pf2wPSCLr3o",
    "subject": "Why Cyprus Is Divided — Europe's Strangest Island",
    "series": "Competitor Rewrite",
    "weight": 1.0,
}


def step_script():
    import naming
    from core.download_competitor import download_video
    from stage2_prompt2_script import generate_script_prompt2
    import flow_stitch as fs

    OUT.mkdir(parents=True, exist_ok=True)
    spec = dict(SPEC)
    video = OUT / "original_video.mp4"
    spec["downloaded_video_path"] = str(video) if video.exists() else download_video(spec["url"], out_dir=str(OUT))
    with open(naming.path(str(OUT), SLUG, "topic_spec"), "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)

    data = generate_script_prompt2(spec)          # includes check_story + retries
    data["topic_id"] = SLUG
    with open(naming.path(str(OUT), SLUG, "script"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    segs = data["segments"]
    print("\n" + "=" * 60)
    print("HOOK:", data.get("hook"))
    print("=" * 60)
    total = 0
    for i, sg in enumerate(segs, 1):
        secs = fs.clip_seconds(sg)
        total += secs
        print(f"{i}. [{secs:.0f}s clip, {len(sg['text'].split())} words] {sg['text']}")
    print("-" * 60)
    print(f"{len(segs)} scenes, clips add up to {total:.0f}s")
    print("\nFlow prompt for scene 2 (what Flow will receive):\n")
    print(fs.build_prompt(segs[1], os.getenv("VIDEO_STYLE", ""), index=2, total=len(segs),
                          seconds=fs.clip_seconds(segs[1])))
    print(f"\nScript saved: {naming.path(str(OUT), SLUG, 'script')}")
    print("If the story reads well:  ./venv/bin/python test_story.py --render")


def step_render():
    import naming
    from flow_stitch import render_reel
    with open(naming.path(str(OUT), SLUG, "script"), encoding="utf-8") as f:
        data = json.load(f)
    reel = render_reel(data, str(OUT), SLUG)
    print(f"\nRENDERED (not published): {reel}")


if __name__ == "__main__":
    step_render() if "--render" in sys.argv else step_script()
