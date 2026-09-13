import json
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

def generate_script(topic):
    """
    Generates a structured 45-second video script, cover art prompt, scene prompts, and SEO metadata.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY missing in environment.")
    
    client = genai.Client(api_key=api_key)
    prompt = f"""
    You are the head scriptwriter and creative director for @dailygeomap, a viral geography and cultural infotainment Reels channel.
    Create a complete 45-second video production package for the topic: "{topic['title']}" (Category: {topic['category']}, Script/Text: {topic['regional_script']}).

    Return ONLY a JSON object with this exact structure:
    {{
      "topic_id": "{topic['id']}",
      "cover_text": "{topic['cover_text']}",
      "regional_script": "{topic['regional_script']}",
      "cover_art_prompt": "High detail 9:16 digital illustration prompt for Midjourney/DALL-E, depicting personified goddess/heroic avatar/majestic temple for {topic['title']}, glowing blue/gold aura, vibrant colors, detailed fantasy artwork, studio lighting",
      "voiceover_text": "Did you know that... [Write a fast-paced 45-second engaging voiceover script in clear, energetic Indian-English]",
      "scenes": [
        {{
          "id": 1,
          "duration": 4.0,
          "visual_prompt": "Cover visual / Fast zoom on glowing title",
          "subtitle": "Did you know this amazing geographical mystery?"
        }},
        {{
          "id": 2,
          "duration": 10.0,
          "visual_prompt": "3D map pan showing geographical origin and route",
          "subtitle": "Geography and origin point details..."
        }},
        {{
          "id": 3,
          "duration": 15.0,
          "visual_prompt": "Cinematic visual of key monuments / rivers / facts",
          "subtitle": "Key cultural and historical facts..."
        }},
        {{
          "id": 4,
          "duration": 10.0,
          "visual_prompt": "Mythological / divine personified illustration",
          "subtitle": "Sacred and cultural significance..."
        }},
        {{
          "id": 5,
          "duration": 6.0,
          "visual_prompt": "Map pin animation and follow prompt",
          "subtitle": "Comment below and follow DailyGeo!"
        }}
      ],
      "seo": {{
        "title": "{topic['title']} | DailyGeo Facts #Shorts",
        "description": "Discover the incredible geography, culture, and history of {topic['title']}! Sub for daily maps & facts.",
        "hashtags": ["#DailyGeo", "#Geography", "#IndiaGeography", "#ReelsIndia", "#Explore", "#ViralReels"]
      }}
    }}
    Do not output markdown code blocks. Just raw JSON.
    """
    
    try:
        response = client.models.generate_content(
            model=os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite"),
            contents=prompt
        )
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        script_data = json.loads(raw)
        print(f"[ScriptGenerator] Successfully generated script via Gemini API for: {topic['title']}")
        return script_data
    except Exception as e:
        print(f"[ScriptGenerator] Gemini API notice: {e}. Using intelligent template fallback.")

    # High-quality fallback script package
    return {
        "topic_id": topic["id"],
        "cover_text": topic.get("cover_text", "DAILY GEO"),
        "regional_script": topic.get("regional_script", topic["title"]),
        "cover_art_prompt": f"Digital illustration for vertical reel cover of {topic['title']}, personified divine character with glowing aura, majestic background, 8k resolution",
        "voiceover_text": f"Did you know the incredible geography of {topic['title']}? Rising through magnificent landscapes, it holds ancient history and sacred heritage. Discover its journey from origin to delta and why it shapes millions of lives daily! Comment below what we should map next, and follow for daily geo facts!",
        "scenes": [
            {"id": 1, "duration": 4.0, "visual_prompt": "Title Zoom", "subtitle": f"Did you know the mystery of {topic['title']}?"},
            {"id": 2, "duration": 10.0, "visual_prompt": "Map origin route", "subtitle": "Origin and geography overview..."},
            {"id": 3, "duration": 15.0, "visual_prompt": "Key landmarks", "subtitle": "Major monuments and cultural landmarks..."},
            {"id": 4, "duration": 10.0, "visual_prompt": "Heritage art", "subtitle": "Sacred significance and history..."},
            {"id": 5, "duration": 6.0, "visual_prompt": "Follow prompt", "subtitle": "Comment below & Follow DailyGeo!"}
        ],
        "seo": {
            "title": f"{topic['title']} | DailyGeo Facts #Shorts",
            "description": f"Explore {topic['title']} geography & history!",
            "hashtags": ["#DailyGeo", "#Geography", "#Shorts", "#Reels"]
        }
    }

if __name__ == "__main__":
    from topic_selector import get_next_topic
    t = get_next_topic()
    s = generate_script(t)
    print(json.dumps(s, indent=2))
