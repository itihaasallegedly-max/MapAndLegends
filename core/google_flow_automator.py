import os
import sys
import json
import time
from playwright.sync_api import sync_playwright

def automate_google_flow(json_path):
    print("Connecting to Chrome via remote debugging...")
    print("(If this fails, ensure you launched Chrome with --remote-debugging-port=9222)")
    
    if not os.path.exists(json_path):
        print(f"Error: Could not find script JSON at {json_path}")
        return
        
    with open(json_path, 'r', encoding='utf-8') as f:
        script_data = json.load(f)
        
    segments = script_data.get("segments", [])
    if not segments:
        print("No 'segments' found in the script JSON.")
        return
        
    print(f"Found {len(segments)} scenes to generate from {json_path}.")
    
    with sync_playwright() as p:
        try:
            print("Connecting to existing Chrome profile via CDP...")
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            if len(context.pages) > 0:
                page = context.pages[0]
            else:
                page = context.new_page()
        except Exception as e:
            print("==========================================================")
            print(f"ERROR: Could not connect to Chrome! Did you launch it with --remote-debugging-port=9222? ({e})")
            print("==========================================================")
            sys.exit(1)
            
        # 1. Navigate to Google Flow (Fresh project for all scenes)
        print("Navigating to https://flow.google.com...")
        page.goto("https://flow.google.com")
        
        # Check if we need to log in
        print("Waiting for page to load. If you see a Google Sign-In page, please log in now!")
        try:
            # Wait until either "Create with Google Flow" OR "New project" is visible, 
            # giving the user up to 2 minutes to log in if they need to.
            page.wait_for_selector("text=Create with Google Flow, text=New project", timeout=120000)
        except:
            print("Timeout waiting for login or page load.")
        
        # Click the main entry button if on landing page
        create_btn = page.locator("text=Create with Google Flow").first
        try:
            create_btn.wait_for(state="visible", timeout=5000)
            create_btn.click()
        except:
            pass
            
        # 2. Click "New project" or skip if already in a project
        try:
            # Check if we are already in a project workspace
            page.locator(".ProseMirror").first.wait_for(state="visible", timeout=3000)
            print("Already in a project workspace! Skipping 'New project' button.")
        except Exception:
            print("Waiting for 'New project' button...")
            new_project_btn = page.locator("text=New project").first
            try:
                new_project_btn.wait_for(state="visible", timeout=15000)
                new_project_btn.click()
            except Exception as e:
                page.screenshot(path="flow_error.png")
                print("Took screenshot of the error at flow_error.png")
                raise e
        # 3. Wait for the empty project workspace
        print("Waiting for the prompt box...")
        editor = page.locator(".ProseMirror").first
        editor.wait_for(state="visible", timeout=20000)

        # Set default configurations
        print("Opening generation settings...")
        try:
            settings_btn = page.locator("button[aria-label='Settings trigger']").first
            settings_btn.click(timeout=5000)
            page.wait_for_timeout(1000)
            
            try: page.get_by_text("Video", exact=True).first.click(timeout=1000)
            except: pass
            try: page.get_by_text("Ingredients", exact=True).first.click(timeout=1000)
            except: pass
            try: page.get_by_text("9:16", exact=True).first.click(timeout=1000) # DailyGeoMap uses Vertical 9:16!
            except: pass
            try: page.get_by_text("Omni 1.1 Flash", exact=True).first.click(timeout=1000)
            except: pass
            try: page.get_by_text("720p", exact=True).first.click(timeout=1000)
            except: pass
            try: page.get_by_text("x1", exact=True).first.click(timeout=1000)
            except: pass
            
            page.keyboard.press("Escape") # Close settings popup
            page.wait_for_timeout(1000) # Wait for backdrop to disappear
        except Exception as e:
            print(f"Note: Could not fully configure settings automatically. ({e})")
            
        # Loop through each scene in the JSON script
        for index, segment in enumerate(segments):
            prompt_text = segment.get("video_prompt", "A cinematic scene")
            narration_text = segment.get("text", "").strip()
            if narration_text:
                prompt_text = f"{prompt_text} Voiceover: {narration_text}"
                
            requested_seconds = float(segment.get("seconds", 8))
            # Estimate audio duration: ~2.3 words per second (conservative ~138 WPM)
            word_count = len(narration_text.split())
            estimated_audio_seconds = word_count / 2.3 if word_count > 0 else 0
            
            # Ensure the video is long enough to fit the narration
            required_duration = max(requested_seconds, estimated_audio_seconds)
            
            # Map required duration to Flow options
            if required_duration <= 4:
                duration_option = "4s"
            elif required_duration <= 6:
                duration_option = "6s"
            elif required_duration <= 8:
                duration_option = "8s"
            else:
                duration_option = "10s"
                
            print(f"\n--- Processing Scene {index+1}/{len(segments)} ---")
            print(f"Target Duration: {duration_option}, Prompt: {prompt_text[:50]}...")
            
            # Open generation settings to set duration
            try:
                settings_btn = page.locator("button[aria-label='Settings trigger']").first
                settings_btn.click(timeout=5000)
                page.wait_for_timeout(1000)
                
                print(f"Selecting duration: {duration_option}")
                page.get_by_text(duration_option, exact=True).first.click(timeout=1000)
                page.keyboard.press("Escape") # Close settings popup
                page.wait_for_timeout(1000) # Wait for backdrop to disappear
            except Exception as e:
                print(f"Note: Could not update duration automatically. ({e})")
            
            # Type prompt and generate
            print("Typing prompt and generating...")
            page.wait_for_timeout(500)
            
            editor.fill(prompt_text)
            page.wait_for_timeout(500)
            
            submit_btn = page.locator("button[aria-label='Start generation']").first
            submit_btn.click()
            
            # Wait for generation to finish
            print("Waiting for AI generation to finish... (Sleeping for 60 seconds)")
            time.sleep(60)
            
            try:
                submit_btn.wait_for(state="attached", timeout=60000)
            except Exception:
                pass
                
            # Check for Retry
            page.wait_for_timeout(3000)
            latest_tile = page.locator("flow-video-tile").first
            retries = 0
            while latest_tile.count() > 0 and latest_tile.locator("button[aria-label='Retry']").first.is_visible() and retries < 3:
                print(f"Generation failed! Clicking Retry... (Attempt {retries + 1})")
                latest_tile.locator("button[aria-label='Retry']").first.evaluate("el => el.click()")
                time.sleep(60)
                try:
                    submit_btn.wait_for(state="attached", timeout=60000)
                except Exception:
                    pass
                page.wait_for_timeout(3000)
                retries += 1
            
            # Optionally wait a bit longer to ensure it lands on the timeline
            page.wait_for_timeout(3000)
            
            # Select the editor again just to be safe
            editor.click()
            # Select all and delete previous prompt so it doesn't append
            page.keyboard.press("Meta+a")
            page.keyboard.press("Backspace")
            
        print("\nAll scenes generated! Now stitching them together onto the timeline...")
        
        # --- Final Assembly ---
        print("Clicking the first scene's video tile to enter Editor view...")
        try:
            tile = page.locator("flow-video-tile").nth(len(segments) - 1)
            tile.wait_for(state="visible", timeout=10000)
            tile.evaluate("el => el.click()")
            page.wait_for_url("**/edit/**", timeout=15000)
        except Exception as e:
            print(f"Warning: Could not enter Editor view automatically: {e}")
            
        num_scenes = len(segments)
        
        # In Flow, the timeline usually keeps the very first clip you generated.
        # We need to add the other clips from the media library.
        for i in range(1, num_scenes):
            scene_index = (num_scenes - 1) - i
            print(f"Adding Scene {i+1} to timeline (Asset index {scene_index})...")
            
            try:
                # 1. Click the main Add clip button
                clicked_add = False
                add_btn = page.locator("[aria-label='Add clip']").first
                try:
                    add_btn.wait_for(state="attached", timeout=5000)
                    add_btn.evaluate("el => el.click()")
                    clicked_add = True
                except Exception:
                    pass
                
                if not clicked_add:
                    print(f"Could not find Add clip button on timeline for scene {i+1}!")
                    continue
                
                # 2. Click the 'Add clip' item inside the dropdown menu
                menu_item = page.locator("span.item-text:has-text('Add clip')").first
                try:
                    menu_item.wait_for(state="attached", timeout=5000)
                    menu_item.evaluate("el => el.click()")
                except Exception:
                    print("Warning: Add clip menu item not found after clicking button.")
                
                # 3. Select asset from the dialog panel
                asset = page.locator("flow-add-menu-asset-item img.asset-thumbnail-image").nth(scene_index)
                asset.wait_for(state="visible", timeout=5000)
                asset.evaluate("el => el.click()")
                page.wait_for_timeout(1000)
                
                # 4. Click Add media
                add_media_btn = page.locator("button:has-text('Add media')").first
                try:
                    add_media_btn.wait_for(state="attached", timeout=5000)
                    add_media_btn.evaluate("el => el.click()")
                except:
                    print("Could not find 'Add media' button, hitting Enter...")
                    page.keyboard.press("Enter")
                    
                print(f"Scene {i+1} added!")
                page.wait_for_timeout(4000)
                
            except Exception as e:
                print(f"Error adding Scene {i+1}: {e}")
                
        print("\nAssembly complete! All clips have been added to the master timeline in your current project.")
        if 'browser' in locals():
            browser.disconnect()
            time.sleep(3)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python core/google_flow_automator.py <path_to_script.json>")
        sys.exit(1)
    
    json_path = sys.argv[1]
    automate_google_flow(json_path)
