#!/usr/bin/env python
import os
import sys
import json
import argparse
from dotenv import load_dotenv

from core.topic_selector import get_next_topic, mark_topic_completed
from core.script_generator import generate_script
from core.image_generator import generate_cover_image
from core.voice_generator import generate_voiceover
from core.video_assembler import assemble_video
from core.uploader import publish_video

load_dotenv()

def run_pipeline(force_topic=None, generate_only=False):
    print("==========================================================")
    print("🚀 DAILYGEOMAP AUTONOMOUS VIDEO FACTORY")
    print("==========================================================")

    # 1. Topic Selection
    if force_topic:
        topic = {
            "id": force_topic.lower().replace(" ", "_"),
            "category": "Custom Topic",
            "title": force_topic,
            "cover_text": force_topic.upper(),
            "regional_script": force_topic,
            "status": "pending"
        }
        print(f"[1/6] Using forced custom topic: {topic['title']}")
    else:
        topic = get_next_topic()
        print(f"[1/6] Topic Selected: {topic['title']} ({topic['category']})")

    output_dir = os.path.join(os.path.dirname(__file__), "outputs", topic["id"])
    os.makedirs(output_dir, exist_ok=True)

    # 2. Script & Visual Specs Generation
    print("\n[2/6] Generating 45s Script & AI Art Prompts via Gemini API...")
    script_data = generate_script(topic)
    script_file = os.path.join(output_dir, "script.json")
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)

    # 3. AI Cover Art & Visual Rendering
    print("\n[3/6] Rendering @dailygeomap Style 9:16 Cover Art...")
    cover_path = generate_cover_image(script_data, output_dir)

    # 4. Voiceover Audio Generation
    print("\n[4/6] Generating Indian-English Voiceover Audio...")
    voice_path = os.path.join(output_dir, "voiceover.mp3")
    generate_voiceover(script_data["voiceover_text"], voice_path)

    # 5. Video Assembly & Motion Filters
    print("\n[5/6] Assembling Vertical 9:16 Reel Video with Motion Effects...")
    video_path = assemble_video(cover_path, voice_path, script_data, output_dir)

    # 6. Publishing / Uploading
    if generate_only:
        print(f"\n[6/6] Pipeline complete in --generate-only mode!")
        print(f"🎬 Ready Video File: {video_path}")
        return video_path

    print("\n[6/6] Publishing Video to YouTube Shorts & Instagram Reels...")
    pub_result = publish_video(video_path, script_data)
    
    if not force_topic:
        mark_topic_completed(topic["id"])

    print("\n==========================================================")
    print("✅ SUCCESS! Video generated and processed successfully.")
    print(f"📁 Video Location: {video_path}")
    print("==========================================================")
    return video_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DailyGeoMap Automated Video Factory")
    parser.add_argument("--topic", type=str, help="Specify a custom topic to generate immediately")
    parser.add_argument("--generate-only", action="store_true", help="Generate video without triggering uploads")
    args = parser.parse_args()

    run_pipeline(force_topic=args.topic, generate_only=args.generate_only)
