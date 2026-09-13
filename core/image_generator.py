"""Cover art and info slides, 1080x1920.

Three bugs fixed here:
  * titles were sliced to 16 characters and then drawn past the right edge,
    because nothing measured the text before drawing it
  * fact cards truncated at 42 characters and still overflowed their box
  * a failed AI image generation silently published placeholder geometry,
    and every slide was watermarked with the reference account's handle
"""
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import naming  # noqa: E402
from pipeline_errors import AssetGenerationError, ConfigError  # noqa: E402

load_dotenv()

W, H = 1080, 1920
MARGIN = 70
# Burned-in captions occupy the bottom of the frame (ASS MarginV=250, up to
# ~5 wrapped lines at 70px). Nothing else may be drawn below this line, or the
# slide content ends up underneath the subtitles.
CONTENT_BOTTOM = H - 700
# Instagram's UI (caption, action rail) overlaps roughly the bottom 320px and
# the top 220px of a Reel; keep anything essential between these.
SAFE_TOP = 110

BOLD_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
# Devanagari / Telugu / Tamil titles need a font that actually has the glyphs.
UNICODE_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font_path(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


NOTDEF_PROBE = "\ufffe"  # a permanent Unicode noncharacter: never in any font


def _glyph_bitmap(font, ch, box=96):
    im = Image.new("L", (box, box), 0)
    ImageDraw.Draw(im).text((box // 8, box // 8), ch, font=font, fill=255)
    return im.tobytes()


def font_supports(font, text):
    """True if this font actually has glyphs for every character in `text`.

    A missing glyph is drawn as a .notdef box rather than raising, so a
    Devanagari or Telugu sub-header silently renders as a row of tofu boxes on
    any machine without an Indic font. Rendering each character and comparing
    it against a codepoint guaranteed to be absent detects that.
    """
    try:
        notdef = _glyph_bitmap(font, NOTDEF_PROBE)
    except Exception:
        return True
    for ch in set(str(text)):
        if ch.isspace() or ord(ch) < 128:
            continue
        try:
            if _glyph_bitmap(font, ch) == notdef:
                return False
        except Exception:
            return False
    return True


def pick_font_for(text, size, candidates):
    """First candidate font that can actually render `text`, else None."""
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
        except OSError:
            continue
        if font_supports(font, text):
            return font
    return None


def load_font(size, candidates=None):
    path = _font_path(candidates or BOLD_FONT_CANDIDATES)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def wrap_to_width(draw, text, font, max_width):
    """Greedy word wrap measured in pixels, not characters."""
    words = str(text).split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        probe = f"{current} {word}"
        if text_width(draw, probe, font) <= max_width:
            current = probe
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_text(draw, text, max_width, max_size, min_size=34, max_lines=2, candidates=None):
    """Largest font size at which `text` wraps into at most `max_lines`.

    Replaces the old approach of slicing the title to 16 characters and
    hoping it fit inside 1080px.
    """
    size = max_size
    pool = candidates or BOLD_FONT_CANDIDATES
    supported = [p for p in pool if os.path.exists(p)]
    if supported:
        probe = pick_font_for(text, max_size, supported)
        if probe is not None:
            pool = [p for p in supported if ImageFont.truetype(p, max_size).getname()
                    == probe.getname()] or supported
    candidates = pool
    while size > min_size:
        font = load_font(size, candidates)
        lines = wrap_to_width(draw, text, font, max_width)
        if len(lines) <= max_lines and all(
            text_width(draw, ln, font) <= max_width for ln in lines
        ):
            return font, lines
        size -= 4
    font = load_font(min_size, candidates)
    lines = wrap_to_width(draw, text, font, max_width)[:max_lines]
    return font, lines


def channel_handle():
    handle = (os.getenv("CHANNEL_HANDLE") or "").strip()
    if not handle:
        raise ConfigError(
            "CHANNEL_HANDLE is not set in .env. It is burned into every slide footer, "
            "and the previous value was @dailygeomap — the reference account, not yours. "
            "Set your own handle before rendering."
        )
    return handle


# ---------------------------------------------------------------- cover art

def generate_cover_image(script_data, output_dir):
    """Render the 3-slide visual sequence. Returns the slide-1 path."""
    os.makedirs(output_dir, exist_ok=True)
    channel_handle()  # fail before spending an API call

    slug = script_data.get("topic_id") or naming.slugify(script_data.get("title", ""))
    raw_img_path = naming.path(output_dir, slug, "cover_raw")
    final_cover_path = naming.path(output_dir, slug, "cover")
    slide2_path = naming.path(output_dir, slug, "slide2")
    slide3_path = naming.path(output_dir, slug, "slide3")

    prompt = script_data.get("cover_art_prompt", "")
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_IMAGE_MODEL")

    errors = []
    success = False

    if api_key and prompt and model_name:
        client = genai.Client(api_key=api_key)
        print(f"[ImageGenerator] Requesting cover art from {model_name}...")

        try:
            result = client.models.generate_images(
                model=model_name,
                prompt=(
                    f"2D vector digital illustration of {prompt}, clean editorial poster "
                    f"style, vibrant background, 9:16 vertical, no text, no lettering"
                ),
                config=types.GenerateImagesConfig(
                    number_of_images=1, aspect_ratio="9:16", output_mime_type="image/jpeg"
                ),
            )
            if result.generated_images:
                with open(raw_img_path, "wb") as f:
                    f.write(result.generated_images[0].image.image_bytes)
                success = True
        except Exception as e:
            errors.append(f"generate_images: {type(e).__name__}: {e}")

        if not success:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=f"Generate a vertical 9:16 vector artwork illustration, no text, for: {prompt}",
                    config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
                )
                for part in (response.candidates or [{}])[0].content.parts:
                    data = getattr(getattr(part, "inline_data", None), "data", None)
                    if data:
                        with open(raw_img_path, "wb") as f:
                            f.write(data)
                        success = True
                        break
            except Exception as e:
                errors.append(f"generate_content: {type(e).__name__}: {e}")
    else:
        errors.append("GEMINI_API_KEY, GEMINI_IMAGE_MODEL or cover_art_prompt missing")

    if success:
        print(f"[ImageGenerator] AI cover art -> {raw_img_path}")
    else:
        allow = os.getenv("ALLOW_PLACEHOLDER_ART", "false").strip().lower() in ("1", "true", "yes")
        detail = "\n  ".join(errors) or "unknown"
        if not allow:
            raise AssetGenerationError(
                "Cover art generation failed and ALLOW_PLACEHOLDER_ART is false, so this "
                "run will not publish placeholder geometry.\n  " + detail + "\n"
                "Run ./venv/bin/python diagnose_api.py to find an image model your key serves."
            )
        print(f"[ImageGenerator] WARNING placeholder art (dev mode). Reasons:\n  {detail}")
        render_placeholder_art(raw_img_path, script_data)

    apply_title_overlay(raw_img_path, final_cover_path, script_data)
    create_infographic_slide(slide2_path, script_data, slide_type="map")
    create_infographic_slide(slide3_path, script_data, slide_type="summary")

    for path in (final_cover_path, slide2_path, slide3_path):
        if not os.path.exists(path):
            raise AssetGenerationError(f"Expected slide was not written: {path}")

    return final_cover_path


def render_placeholder_art(output_path, script_data):
    """Dev-only background. Deliberately plain — it is not meant to be published."""
    topic = str(script_data.get("topic_id", "")).lower()
    if any(k in topic for k in ("river", "water", "sea", "ocean", "lake", "delta")):
        top, bottom, accent = (10, 35, 65), (20, 80, 140), (0, 210, 190)
    elif any(k in topic for k in ("temple", "sacred", "heritage", "fort", "shrine")):
        top, bottom, accent = (60, 28, 12), (150, 75, 25), (240, 190, 70)
    else:
        top, bottom, accent = (22, 18, 44), (80, 34, 100), (225, 110, 180)

    img = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line(
            [(0, y), (W, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = W // 2, int(H * 0.56)
    for r in range(460, 60, -12):
        gd.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(*accent, int(90 * (1 - r / 460))),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(24))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    draw = ImageDraw.Draw(img)
    draw.rectangle([MARGIN // 2, MARGIN // 2, W - MARGIN // 2, H - MARGIN // 2],
                   outline=accent, width=4)
    font = load_font(40)
    label = "PLACEHOLDER ART — NOT FOR PUBLISHING"
    draw.text((W // 2, H - 120), label, font=font, fill=(255, 255, 255), anchor="mm")
    img.save(output_path, quality=94)
    print(f"[ImageGenerator] placeholder -> {output_path}")


def apply_title_overlay(input_path, output_path, script_data):
    """Glowing headline, wrapped and auto-sized to actually fit the frame."""
    img = Image.open(input_path).convert("RGBA").resize((W, H))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    title = str(
        script_data.get("cover_text") or script_data.get("title") or "DAILY GEO"
    ).upper().replace("_", " ").strip()
    regional = str(script_data.get("regional_script") or "").strip()

    box_w = W - 2 * MARGIN
    title_font, title_lines = fit_text(draw, title, box_w, max_size=104, min_size=46, max_lines=2)
    line_h = int(title_font.size * 1.12)

    sub_lines, sub_font = [], None
    if regional and regional.upper() != title:
        if pick_font_for(regional, 72, UNICODE_FONT_CANDIDATES) is None:
            print(
                f"[ImageGenerator] WARNING no installed font has glyphs for "
                f"{regional!r} — skipping the regional sub-header rather than "
                f"drawing empty boxes. Install a font covering that script and "
                f"add it to UNICODE_FONT_CANDIDATES."
            )
        else:
            sub_font, sub_lines = fit_text(
                draw, regional, box_w, max_size=72, min_size=36, max_lines=1,
                candidates=UNICODE_FONT_CANDIDATES,
            )

    block_h = len(title_lines) * line_h + (int(sub_font.size * 1.3) if sub_lines else 0)
    banner_top = 90
    banner_bottom = banner_top + block_h + 70
    draw.rectangle([0, banner_top, W, banner_bottom], fill=(0, 0, 0, 170))

    y = banner_top + 42 + line_h // 2
    for line in title_lines:
        for dx in range(-8, 9, 4):
            for dy in range(-8, 9, 4):
                draw.text((W // 2 + dx, y + dy), line, font=title_font,
                          fill=(0, 245, 212, 150), anchor="mm")
        for dx in range(-4, 5, 2):
            for dy in range(-4, 5, 2):
                draw.text((W // 2 + dx, y + dy), line, font=title_font,
                          fill=(0, 0, 0, 255), anchor="mm")
        draw.text((W // 2, y), line, font=title_font, fill=(255, 235, 20, 255), anchor="mm")
        y += line_h

    for line in sub_lines:
        draw.text((W // 2, y + int(sub_font.size * 0.2)), line, font=sub_font,
                  fill=(255, 255, 255, 240), anchor="mm")

    Image.alpha_composite(img, overlay).convert("RGB").save(output_path, quality=94)
    print(
        f"[ImageGenerator] title overlay -> {output_path} "
        f"({len(title_lines)} line(s) at {title_font.size}px)"
    )


def create_infographic_slide(output_path, script_data, slide_type="map"):
    """Slide 2 (map/overview) or slide 3 (fact summary), with wrapped text."""
    handle = channel_handle()

    img = Image.new("RGB", (W, H), (15, 25, 45))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)],
                  fill=(int(15 + 25 * t), int(25 + 35 * t), int(45 + 45 * t)))

    header_font = load_font(72)
    body_font = load_font(42)
    num_font = load_font(64)

    cyan, gold, ink = (0, 245, 212), (255, 200, 50), (10, 15, 30)
    card_l, card_r = MARGIN, W - MARGIN
    inner_w = card_r - card_l

    heading = "GEOGRAPHY & MAP OVERVIEW" if slide_type == "map" else "EXAM FACTS SUMMARY"
    hfont, hlines = fit_text(draw, heading, inner_w - 40, max_size=72, min_size=38, max_lines=1)
    draw.rectangle([card_l, 120, card_r, 250], fill=cyan if slide_type == "map" else gold,
                   outline=(255, 255, 255), width=4)
    draw.text(((card_l + card_r) // 2, 185), hlines[0], font=hfont, fill=ink, anchor="mm")

    facts = [str(f) for f in script_data.get("facts", []) if str(f).strip()]

    if slide_type == "map":
        # The map panel fills the content area on its own. It must NOT be
        # captioned with segment["visual"] values — those are stage directions
        # for the renderer ("Four symbol cards over a map of Jharkhand"), not
        # something a viewer should ever read.
        draw.rectangle([card_l, 320, card_r, CONTENT_BOTTOM], fill=(20, 35, 60),
                       outline=cyan, width=5)
        cx, cy = W // 2, (320 + CONTENT_BOTTOM) // 2
        r = min((CONTENT_BOTTOM - 320) // 2 - 40, 300)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(30, 80, 140),
                     outline=gold, width=6)
        label = str(script_data.get("cover_text") or "MAP").upper()
        lf, ll = fit_text(draw, label, int(r * 1.7), max_size=64, min_size=30, max_lines=2)
        ly = cy - (len(ll) - 1) * int(lf.size * 0.6)
        for line in ll:
            draw.text((cx, ly), line, font=lf, fill=(255, 255, 255), anchor="mm")
            ly += int(lf.size * 1.2)
        items, y = [], CONTENT_BOTTOM
    else:
        items, y = facts[:4], 320

    for idx, item in enumerate(items, 1):
        lines = wrap_to_width(draw, item, body_font, inner_w - 150)[:3]
        line_h = int(body_font.size * 1.32)
        card_h = max(130, len(lines) * line_h + 56)
        if y + card_h > CONTENT_BOTTOM:
            print(f"[ImageGenerator] {len(items) - idx + 1} fact(s) dropped from the "
                  f"slide — they would sit under the captions")
            break
        draw.rectangle([card_l, y, card_r, y + card_h], fill=(25, 40, 70), outline=cyan, width=3)
        draw.rectangle([card_l, y, card_l + 86, y + card_h], fill=cyan)
        draw.text((card_l + 43, y + card_h // 2), str(idx), font=num_font, fill=ink, anchor="mm")
        ty = y + (card_h - len(lines) * line_h) // 2 + line_h // 2
        for line in lines:
            draw.text((card_l + 116, ty), line, font=body_font, fill=(255, 255, 255), anchor="lm")
            ty += line_h
        y += card_h + 26

    footer_font = load_font(40)
    draw.text((W // 2, H - 90), handle, font=footer_font, fill=(200, 215, 215), anchor="mm")
    img.save(output_path, quality=94)
    print(f"[ImageGenerator] slide ({slide_type}) -> {output_path}")


if __name__ == "__main__":
    os.environ.setdefault("ALLOW_PLACEHOLDER_ART", "true")
    demo = {
        "cover_text": "TEMPLES OF ANDHRA PRADESH",
        "title": "TEMPLES OF ANDHRA PRADESH",
        "regional_script": "ఆంధ్రప్రదేశ్ దేవాలయాలు",
        "topic_id": "temples_of_andhra_pradesh",
        "cover_art_prompt": "temples of Andhra Pradesh",
        "segments": [{"visual": "Map of Andhra Pradesh with temple pins"}],
        "facts": [
            "The Venkateswara temple at Tirumala sits in the Seshachalam hills of Tirupati district.",
            "Lepakshi's Veerabhadra temple was built under the Vijayanagara empire in the 16th century.",
            "Srisailam's Mallikarjuna temple is one of the twelve Jyotirlingas.",
        ],
    }
    generate_cover_image(demo, os.path.join(os.path.dirname(BASE := os.path.dirname(os.path.abspath(__file__))), "outputs", "_render_test"))
