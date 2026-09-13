import json
import os
import random
from dotenv import load_dotenv
from google import genai

load_dotenv()

QUEUE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "content_queue.json")

def get_next_topic():
    """
    Selects the next pending topic from content_queue.json.
    If no topics are pending, calls Gemini API to auto-generate a new viral topic.
    """
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        pending = [t for t in data.get("topics", []) if t.get("status") == "pending"]
        if pending:
            selected = pending[0]
            print(f"[TopicSelector] Selected topic from queue: {selected['title']}")
            return selected

    print("[TopicSelector] Queue empty/missing. Auto-generating viral topic via Gemini API...")
    return generate_dynamic_topic()

def mark_topic_completed(topic_id):
    """Marks a topic as completed in content_queue.json."""
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for t in data.get("topics", []):
            if t.get("id") == topic_id:
                t["status"] = "completed"
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[TopicSelector] Marked topic '{topic_id}' as completed.")

def generate_dynamic_topic():
    """Generates a dynamic trending geo-infotainment topic using Gemini API."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing in environment.")
    
    client = genai.Client(api_key=api_key)
    prompt = """
    Generate 1 viral video topic for an Instagram Reels / YouTube Shorts channel focused on geography, sacred rivers, heritage temples, and cultural civics in India (style of @dailygeomap).
    Return ONLY a JSON object with this exact structure:
    {
      "id": "unique_slug",
      "category": "Sacred Rivers OR Architectural Heritage OR State Profiles OR Global Geography OR Symbols & Civics",
      "title": "Short Catchy Topic Title",
      "cover_text": "UPPERCASE COVER TITLE",
      "regional_script": "Title in Hindi / Telugu / Tamil / Assamese script if Indian state/river, else English",
      "keywords": ["keyword1", "keyword2", "keyword3"],
      "status": "pending"
    }
    Do not include markdown backticks or explanation. Just raw JSON.
    """
    response = client.models.generate_content(
        model=os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite"),
        contents=prompt
    )
    raw = response.text.strip().replace("```json", "").replace("```", "").strip()
    topic = json.loads(raw)
    print(f"[TopicSelector] Auto-generated topic: {topic['title']}")
    return topic

if __name__ == "__main__":
    t = get_next_topic()
    print("Topic:", t)
