import os
import json
import time
from playwright.sync_api import sync_playwright

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs", "scraped_reel_analysis")

def record_and_analyze_reel():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("==========================================================")
    print("🎥 PLAYWRIGHT SCREEN RECORDING & CONTENT CAPTURE ENGINE")
    print("==========================================================")

    with sync_playwright() as p:
        # Desktop viewport to bypass mobile app redirect banner
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=OUTPUT_DIR,
            record_video_size={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print("[1/5] Navigating to https://www.instagram.com/dailygeomap/...")
        try:
            page.goto("https://www.instagram.com/dailygeomap/", wait_until="networkidle", timeout=45000)
            time.sleep(4)
        except Exception as e:
            print(f"[RecordEngine] Page load notice: {e}")

        # Capture profile view
        profile_shot = os.path.join(OUTPUT_DIR, "01_profile_scroll.png")
        page.screenshot(path=profile_shot, full_page=False)
        print(f"[2/5] Saved profile screenshot -> {profile_shot}")

        # Close any dialog or cookie popup if present
        try:
            close_buttons = page.query_selector_all("button:has-text('Decline'), button:has-text('Not Now'), svg[aria-label='Close']")
            for btn in close_buttons:
                btn.click()
                time.sleep(1)
        except Exception:
            pass

        print("[3/5] Locating video posts in grid...")
        video_details = {
            "channel": "@dailygeomap",
            "url": page.url,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "captured_captions": [],
            "on_screen_texts": []
        }

        # Try multiple selectors for grid post tiles
        post_elements = page.query_selector_all("a[href*='/p/'], a[href*='/reel/'], div._aabd, div._aagv")
        
        if not post_elements:
            # Fallback: find any clickable grid image
            post_elements = page.query_selector_all("article img, main img")

        if post_elements:
            target_post = post_elements[0]
            print(f"[4/5] Found {len(post_elements)} posts. Clicking featured video post...")
            try:
                target_post.click()
                print("[RecordEngine] Post clicked! Playing video and recording playback for 15 seconds...")
                time.sleep(3)

                # Capture video playback frame 1
                frame1 = os.path.join(OUTPUT_DIR, "02_video_playback_frame_1.png")
                page.screenshot(path=frame1)
                print(f"[RecordEngine] Captured Playback Frame 1 -> {frame1}")

                # Wait for mid-video playback
                time.sleep(7)
                frame2 = os.path.join(OUTPUT_DIR, "03_video_playback_frame_2.png")
                page.screenshot(path=frame2)
                print(f"[RecordEngine] Captured Playback Frame 2 -> {frame2}")

                time.sleep(5)
            except Exception as e:
                print(f"[RecordEngine] Playback interaction note: {e}")
        else:
            print("[RecordEngine] Note: Grid rendered in fallback view; proceeding with full DOM content analysis.")

        # Extract visible captions, headings, and paragraph text
        print("[5/5] Extracting video captions & on-screen text overlays...")
        texts = page.evaluate("""() => {
            const nodes = Array.from(document.querySelectorAll('span, h1, h2, h3, p, div[role="dialog"]'));
            return nodes.map(n => n.innerText.trim()).filter(t => t.length > 2);
        }""")
        
        # Deduplicate & filter irrelevant UI text
        unique_texts = []
        for t in texts:
            if t not in unique_texts and not any(w in t.lower() for w in ["log in", "sign up", "cookie", "meta"]):
                unique_texts.append(t)

        video_details["captured_captions"] = unique_texts[:40]

        analysis_json = os.path.join(OUTPUT_DIR, "reel_content_analysis.json")
        with open(analysis_json, "w", encoding="utf-8") as f:
            json.dump(video_details, f, indent=2, ensure_ascii=False)

        context.close()
        browser.close()

        print("\n==========================================================")
        print("✅ SCREEN RECORDING & CAPTION EXTRACTION COMPLETE!")
        print(f"📁 Video & Screenshots saved to: {OUTPUT_DIR}")
        print("==========================================================")

if __name__ == "__main__":
    record_and_analyze_reel()

