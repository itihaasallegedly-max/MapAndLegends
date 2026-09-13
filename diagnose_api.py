#!/usr/bin/env python
"""
Run this from your own terminal (not from a Claude session — the sandbox
cannot reach generativelanguage.googleapis.com).

    ./venv/bin/python diagnose_api.py

It answers one question: which model IDs does this API key actually serve?
Nothing else in the pipeline works until that is settled.
"""
import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


def _get(path, key):
    req = urllib.request.Request(f"{API_ROOT}/{path}?key={key}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path, key, payload):
    req = urllib.request.Request(
        f"{API_ROOT}/{path}?key={key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set in .env")

    print("=" * 62)
    print("GEMINI API DIAGNOSTIC")
    print("=" * 62)
    print(f"Key: ...{key[-6:]}  (length {len(key)})")

    try:
        data = _get("models", key)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        print(f"\nListModels FAILED: HTTP {e.code}")
        print(body)
        if e.code in (400, 403):
            print(
                "\n-> The key itself is being rejected. Create a fresh key at "
                "https://aistudio.google.com/apikey and make sure the "
                "Generative Language API is enabled on that project."
            )
        sys.exit(1)
    except Exception as e:
        sys.exit(f"\nListModels FAILED: {e}")

    models = data.get("models", [])
    print(f"\nKey is valid. {len(models)} models visible.\n")

    text_models, image_models = [], []
    for m in models:
        name = m["name"].removeprefix("models/")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods:
            text_models.append(name)
        if "predict" in methods or "generateImages" in methods or "image" in name:
            image_models.append(name)

    print("--- TEXT (generateContent) " + "-" * 34)
    for n in text_models:
        print("   ", n)
    print("\n--- IMAGE-CAPABLE " + "-" * 43)
    for n in image_models:
        print("   ", n)

    configured_text = os.getenv("GEMINI_TEXT_MODEL", "")
    configured_image = os.getenv("GEMINI_IMAGE_MODEL", "")

    print("\n" + "=" * 62)
    print("YOUR .env vs REALITY")
    print("=" * 62)
    for label, configured, available in (
        ("GEMINI_TEXT_MODEL", configured_text, text_models),
        ("GEMINI_IMAGE_MODEL", configured_image, image_models),
    ):
        ok = configured in available
        print(f"{label} = {configured!r}  ->  {'OK' if ok else 'NOT SERVED BY THIS KEY'}")
        if not ok and available:
            print(f"    pick one of the {label.split('_')[1].lower()} models listed above")

    if configured_text in text_models:
        print("\nLive round-trip test on the configured text model...")
        try:
            r = _post(
                f"models/{configured_text}:generateContent",
                key,
                {"contents": [{"parts": [{"text": "Reply with the single word: ok"}]}]},
            )
            got = r["candidates"][0]["content"]["parts"][0]["text"].strip()
            print(f"  response: {got!r}  -> text generation WORKS")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except Exception as e:
            print(f"  failed: {e}")

    print("\nPut the working IDs in .env, then run:")
    print("  ./venv/bin/python stage2_prompt2_script.py")
    print("It will now raise instead of silently returning a template.\n")


if __name__ == "__main__":
    main()
