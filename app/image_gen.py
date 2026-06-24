"""AI image generation — professional app marketing composite.

Pipeline:
1. Build a scenario prompt (person in environment, NO phone in hand).
2. Call Pollinations.ai FLUX-pro for a 1080x1080 background photo.
3. Composite a phone mockup with the real app screenshot on the right.
4. Add a dark bottom panel: app icon · feature name · subtitle · stats · CTA.

Everything is best-effort: any failure returns None so the caller falls back
to the legacy Pillow renderer. This module NEVER raises.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import get_settings

log = logging.getLogger("autopost")
settings = get_settings()

OUT_DIR = Path(__file__).resolve().parent.parent / "generated"
OUT_DIR.mkdir(exist_ok=True)

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"

# Background prompts: person/environment only, NO phone in hand.
# The phone is composited separately with the real app screenshot.
PROMPT_SUFFIX = (
    ", aerial or wide landscape photography, Drone DJI shot or Canon EOS R5, "
    "ultra sharp, vivid colors, cinematic lighting, "
    "no people, no text, no watermarks, square 1:1"
)

# Scenic/environment backgrounds — NO people. AI generates landscapes far more
# reliably than humans. The phone mockup supplies the "person using the app" context.
SCENARIO_PROMPTS: dict[str, str] = {
    "area-measurement": (
        "Stunning aerial drone view of vast green agricultural farmland in India, "
        "geometric crop field patterns visible from above, lush green and yellow fields, "
        "dirt paths between plots, golden hour warm light casting long shadows, "
        "blue sky with scattered white clouds reflecting on the land"
    ),
    "distance-tracking": (
        "Breathtaking aerial view of a winding hiking trail through dense pine forest "
        "and mountain terrain, blue river visible in the valley below, "
        "misty mountain peaks in the distance, bright morning sunlight"
    ),
    "poi-markers": (
        "Vibrant aerial top-down view of a colorful city intersection and streets, "
        "cars and pedestrians below, bright storefronts with awnings, "
        "warm afternoon golden light, birds-eye urban photography"
    ),
    "route-planner": (
        "Cinematic wide-angle view of a smooth multi-lane highway stretching into the "
        "horizon through a scenic landscape, golden sunset sky with orange and pink hues, "
        "road markings leading into the distance, motion blur on sides"
    ),
    "voice-navigation": (
        "First-person driver POV on an open highway at sunset, dashboard blurred in "
        "foreground, wide highway curving into glowing orange horizon, "
        "dramatic sky with warm rays of light, cinematic depth of field"
    ),
    "speedometer": (
        "Dramatic motion-blur shot of a road rushing beneath a motorcycle, "
        "speed lines from fast movement, city lights streaking in bokeh background, "
        "dark moody asphalt with white lane markings, dynamic energy"
    ),
    "compass": (
        "Majestic aerial panoramic view of Himalayan mountain peaks and valleys, "
        "snow-capped summits rising above clouds, dramatic blue sky, "
        "deep valleys with green forests below, adventure landscape"
    ),
    "gps-camera": (
        "Wide aerial view of an open green countryside with fields, trees and a winding "
        "river, patchwork of land plots from above, bright afternoon sunshine, "
        "vivid natural colors, professional drone photography"
    ),
    "gps-gallery": (
        "Beautiful golden hour landscape with a tranquil lake reflecting orange and pink "
        "sunset sky, trees silhouetted on the banks, misty soft light, "
        "travel photography mood, National Geographic style"
    ),
    "wonder-places": (
        "Stunning wide-angle photo of the Taj Mahal at golden hour, "
        "perfectly symmetrical reflection pool leading to the white marble monument, "
        "warm orange and pink sky, lush gardens on either side, "
        "iconic India heritage, professional architectural photography"
    ),
    "street-view": (
        "Wide-angle view down a beautiful cobblestone European city street, "
        "historic colorful buildings on both sides, warm evening cafe lights, "
        "blurred bokeh of street lamps, romantic travel destination atmosphere"
    ),
    "groups": (
        "Aerial drone view of large green farmland with neat rows of crops, "
        "irrigation channels visible, farmhouses in distance, "
        "bright midday sunlight, productive agricultural landscape"
    ),
    "saved-measurements": (
        "Clean flat-lay aerial view of architectural blueprints and measuring tools "
        "on a desk with a clipboard, precise geometry, "
        "bright professional studio lighting, minimal and sharp"
    ),
    "nearby-location": (
        "Aerial top-down view of a busy city downtown area at dusk, "
        "street lights turning on, colorful neon signs of restaurants and shops, "
        "cars with headlights creating light trails, urban lifestyle atmosphere"
    ),
    "find-routes": (
        "Aerial view of a complex highway interchange with multiple roads and bridges, "
        "smooth flowing traffic, green landscape surrounding the interchange, "
        "golden hour light, dramatic infrastructure photography"
    ),
}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
def build_scenario_prompt(feature: dict, angle: dict, content: dict) -> str:
    feature_id = (feature or {}).get("id", "")
    base = SCENARIO_PROMPTS.get(feature_id)
    if not base:
        benefit = (feature or {}).get("primary_benefit") or (feature or {}).get("short") or ""
        base = (
            "Person outdoors in a relevant environment for a GPS navigation app, "
            + (f"{benefit.rstrip('.')}, " if benefit else "")
            + "professional lifestyle photography"
        )
    return base + PROMPT_SUFFIX


def _seed_for(post_key: str, prompt: str) -> int:
    basis = f"{post_key}|{prompt}".encode("utf-8")
    return int(hashlib.sha256(basis).hexdigest()[:8], 16) % 1_000_000


# ---------------------------------------------------------------------------
# AI image fetch
# ---------------------------------------------------------------------------
def _generate_pollinations(prompt: str, seed: int, width: int, height: int) -> Optional[bytes]:
    encoded = urllib.parse.quote(prompt, safe="")
    qs = urllib.parse.urlencode(
        {"width": width, "height": height, "seed": seed, "model": "flux-pro", "nologo": "true"}
    )
    url = f"{POLLINATIONS_URL.format(prompt=encoded)}?{qs}"
    log.info("pollinations request seed=%s url=%s", seed, url[:160])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "autopost/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        return data or None
    except Exception as e:  # noqa: BLE001
        log.warning("pollinations failed: %s", e)
        return None


def _generate_openai(prompt: str, width: int, height: int) -> Optional[bytes]:
    try:
        import openai
    except ImportError:
        log.warning("openai package not installed")
        return None
    try:
        client = openai.OpenAI(api_key=settings.openai_api_key)
        response = client.images.generate(
            model="dall-e-3", prompt=prompt, size="1024x1024", quality="standard", n=1
        )
        image_url = response.data[0].url
        req = urllib.request.Request(image_url, headers={"User-Agent": "autopost/1.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001
        log.warning("openai DALL-E 3 failed: %s", e)
        return None


def generate_ai_image(prompt: str, seed: int, width: int = 1080, height: int = 1080) -> Optional[bytes]:
    if settings.image_gen_provider == "openai" and settings.openai_api_key:
        data = _generate_openai(prompt, width, height)
        if data:
            return data
        log.warning("openai failed; trying pollinations")
    return _generate_pollinations(prompt, seed, width, height)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rgb(hex_str: str) -> tuple[int, int, int]:
    h = (hex_str or "#0aa77f").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (10, 167, 127)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/Library/Fonts/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["/System/Library/Fonts/Supplemental/Arial.ttf",
         "/Library/Fonts/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and draw.textlength(candidate, font=font) > max_w:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _draw_phone_mockup(screenshot_path: str, accent: tuple[int, int, int],
                       phone_w: int = 320, phone_h: int = 640) -> Image.Image:
    """Return a transparent-background RGBA image of a phone mockup with the screenshot inside."""
    canvas = Image.new("RGBA", (phone_w, phone_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    radius = 38
    # Drop shadow
    shadow = Image.new("RGBA", (phone_w, phone_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([6, 8, phone_w - 2, phone_h - 2], radius=radius, fill=(0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    canvas.alpha_composite(shadow)

    # Phone body
    d.rounded_rectangle([0, 0, phone_w - 8, phone_h - 8], radius=radius, fill=(18, 18, 22, 255))
    # Highlight edge
    d.rounded_rectangle([2, 2, phone_w - 10, phone_h - 10], radius=radius - 2,
                         outline=(70, 70, 80, 200), width=2)

    # Screen inset
    bx, by = 14, 44
    bx2, by2 = phone_w - 22, phone_h - 44
    screen_w, screen_h = bx2 - bx, by2 - by

    # Speaker notch
    notch_w, notch_h = 60, 14
    nx = (phone_w - 8 - notch_w) // 2
    d.rounded_rectangle([nx, 14, nx + notch_w, 14 + notch_h], radius=7, fill=(10, 10, 14, 255))

    # Home indicator
    ind_w = 72
    ix = (phone_w - 8 - ind_w) // 2
    d.rounded_rectangle([ix, phone_h - 26, ix + ind_w, phone_h - 16], radius=5, fill=(80, 80, 90, 200))

    # Screenshot or accent fill
    screen_mask = Image.new("L", (screen_w, screen_h), 0)
    ImageDraw.Draw(screen_mask).rounded_rectangle([0, 0, screen_w - 1, screen_h - 1], radius=6, fill=255)

    if screenshot_path and Path(str(screenshot_path)).exists():
        try:
            ss = Image.open(str(screenshot_path)).convert("RGB")
            ss = ss.resize((screen_w, screen_h), Image.LANCZOS)
            canvas.paste(ss, (bx, by), screen_mask)
        except Exception as e:  # noqa: BLE001
            log.warning("phone screenshot paste failed: %s", e)
            fill_layer = Image.new("RGB", (screen_w, screen_h), accent)
            canvas.paste(fill_layer, (bx, by), screen_mask)
    else:
        fill_layer = Image.new("RGB", (screen_w, screen_h), accent)
        canvas.paste(fill_layer, (bx, by), screen_mask)

    return canvas


# ---------------------------------------------------------------------------
# Main composite builder
# ---------------------------------------------------------------------------
def create_marketing_composite(img_bytes: bytes, slide: dict) -> Image.Image:
    """
    Professional app marketing composite layout:

    ┌─────────────────────────────────┐
    │  AI background photo (full bleed│  ← person in scenario, no phone
    │                    ┌──────────┐ │
    │                    │  PHONE   │ │  ← real app screenshot in mockup
    │                    │ MOCKUP   │ │
    │                    └──────────┘ │
    ├─────────────────────────────────┤
    │ [icon] App Name                 │  ← dark panel
    │ Feature Title (large)           │
    │ Subtitle / benefit              │
    │ 10M+ users                      │
    │ [  Install Free  →  ]           │
    └─────────────────────────────────┘
    """
    W, H = 1080, 1080
    PANEL_H = 280       # bottom dark panel height
    PHOTO_H = H - PANEL_H

    accent = _rgb(slide.get("accent", "#0aa77f"))

    # --- Background photo ---
    bg = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    # Crop to photo area only (top portion), then resize to full width
    bg = bg.resize((W, H), Image.LANCZOS)
    # Crop photo section taller so the person isn't cut
    photo_section = bg.crop((0, 0, W, PHOTO_H + 60)).resize((W, PHOTO_H + 60), Image.LANCZOS)
    photo_section = photo_section.crop((0, 0, W, PHOTO_H))

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    canvas.paste(photo_section.convert("RGBA"), (0, 0))

    # Subtle gradient at photo bottom to blend into dark panel
    grad_h = 120
    grad = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
    for y in range(grad_h):
        alpha = int((y / grad_h) ** 1.5 * 200)
        ImageDraw.Draw(grad).line([(0, y), (W - 1, y)], fill=(10, 12, 18, alpha))
    canvas.alpha_composite(grad, (0, PHOTO_H - grad_h))

    # --- Dark bottom panel ---
    panel = Image.new("RGBA", (W, PANEL_H), (10, 12, 20, 250))
    canvas.alpha_composite(panel, (0, PHOTO_H))

    # Accent top border line on panel
    border = Image.new("RGBA", (W, 4), (*accent, 255))
    canvas.alpha_composite(border, (0, PHOTO_H))

    # --- Phone mockup ---
    phone_w, phone_h = 300, 600
    phone = _draw_phone_mockup(
        slide.get("screenshot_path", ""),
        accent, phone_w, phone_h
    )
    # Position: right side, spanning photo/panel boundary — guarantee bottom stays in canvas
    phone_x = W - phone_w - 44
    phone_y = PHOTO_H - int(phone_h * 0.72)   # 72% in photo, 28% in panel
    phone_y = max(20, min(phone_y, H - phone_h - 10))   # clamp to canvas bounds
    canvas.alpha_composite(phone, (phone_x, phone_y))

    # --- Text in bottom panel ---
    draw = ImageDraw.Draw(canvas)
    pad = 52
    text_right_limit = phone_x - 28   # don't let text overlap phone

    y = PHOTO_H + 22

    # Row 1: app icon + app name
    icon_size = 62
    icon_path = slide.get("app_icon_path", "")
    if icon_path and Path(str(icon_path)).exists():
        try:
            icon = Image.open(str(icon_path)).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
            mask = Image.new("L", (icon_size, icon_size), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, icon_size, icon_size], radius=14, fill=255)
            canvas.paste(icon, (pad, y), mask)
        except Exception as e:  # noqa: BLE001
            log.warning("icon paste failed: %s", e)

    app_name = (slide.get("app_name") or "").strip()
    if app_name:
        draw.text((pad + icon_size + 14, y + 14), app_name,
                  font=_font(28, bold=True), fill=(180, 195, 215))

    y += icon_size + 10

    # Row 2: Feature title (large)
    title = (slide.get("title") or "").strip()
    title_font = _font(54, bold=True)
    max_w = text_right_limit - pad
    for line in _wrap(draw, title, title_font, max_w)[:2]:
        draw.text((pad, y), line, font=title_font, fill=(255, 255, 255))
        y += 62

    # Row 3: subtitle (1 line)
    subtitle = (slide.get("subtitle") or "").strip()
    if subtitle:
        sub_font = _font(28, bold=False)
        sub_lines = _wrap(draw, subtitle, sub_font, max_w)[:1]
        for line in sub_lines:
            draw.text((pad, y), line, font=sub_font, fill=(160, 178, 198))
            y += 36
        y += 4

    # No stats badge, no CTA button — clean minimal panel.

    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
def generate_realistic_post(
    feature: dict, angle: dict, post_key: str, slide: dict, content: dict
) -> Optional[str]:
    """Build prompt → fetch AI photo → composite marketing image → save PNG. Returns path or None."""
    try:
        prompt = build_scenario_prompt(feature, angle, content or {})
        seed = _seed_for(post_key, prompt)
        img_bytes = generate_ai_image(prompt, seed)
        if not img_bytes:
            return None
        final = create_marketing_composite(img_bytes, slide)
        out_path = OUT_DIR / f"{post_key}_ai.png"
        final.save(out_path)
        log.info("marketing composite saved: %s", out_path)
        return str(out_path)
    except Exception as e:  # noqa: BLE001
        log.warning("generate_realistic_post failed for %s: %s", post_key, e)
        return None
