"""AI image generation — professional app marketing composite.

Pipeline:
1. Build a scenario prompt for a realistic environment.
2. Call Pollinations.ai FLUX-pro for a 1080x1080 background photo.
3. Add a clean feature-specific marketing overlay.
4. Use app mockups only on selected variants, never on every post.

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

# Background prompts: realistic environment. Any app UI is composited separately
# only on selected variants.
PROMPT_SUFFIX = (
    ", premium realistic commercial photography, cinematic natural light, "
    "ultra sharp, vivid but natural colors, no text, no logos, no watermarks, square 1:1"
)

# Scenic/environment backgrounds. The app UI is composited separately only when a
# real matching screenshot is available, so the AI never invents fake UI.
SCENARIO_PROMPTS: dict[str, str] = {
    "area-measurement": (
        "Real Indian farmer standing in a green crop field holding a smartphone, "
        "wide rural farmland, clear field boundaries, warm sunrise light, practical "
        "land measurement work, documentary commercial photography"
    ),
    "distance-tracking": (
        "Field survey worker in safety vest walking along a farm boundary with a "
        "smartphone, open agricultural land, visible path line direction, warm "
        "golden hour, realistic commercial photography"
    ),
    "poi-markers": (
        "Delivery rider and small business storefront street scene, smartphone in "
        "hand, important place marking context, warm city daylight, realistic "
        "lifestyle commercial photography"
    ),
    "route-planner": (
        "Driver planning a multi stop trip inside a clean car, highway and city roads "
        "visible through windshield, sunset travel mood, realistic cinematic photo"
    ),
    "voice-navigation": (
        "Driver using hands free navigation on an open highway, one hand safely on "
        "steering wheel, warm sunset road, cinematic depth of field, realistic photo"
    ),
    "speedometer": (
        "Motorcycle rider on a clear road with motion blur, speed tracking context, "
        "city lights in background, dynamic realistic commercial photography"
    ),
    "compass": (
        "Outdoor traveler holding a phone while standing on a mountain viewpoint, "
        "clear directional exploration context, dramatic sky, realistic adventure photo"
    ),
    "gps-camera": (
        "Construction site inspector taking a photo with smartphone, visible location "
        "proof context, road work and field boundary behind, realistic daylight photo"
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
        "Two field workers reviewing multiple land plots on a smartphone in green "
        "farmland, organized project work, bright natural light, realistic photo"
    ),
    "saved-measurements": (
        "Land surveyor at a desk with printed field maps, measuring tape and phone, "
        "saved project records context, clean professional daylight photography"
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
                       phone_w: int = 320, phone_h: int = 640) -> Image.Image | None:
    """Return a transparent-background RGBA image of a phone mockup with the screenshot inside."""
    if not screenshot_path or not Path(str(screenshot_path)).exists():
        return None
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

    screen_mask = Image.new("L", (screen_w, screen_h), 0)
    ImageDraw.Draw(screen_mask).rounded_rectangle([0, 0, screen_w - 1, screen_h - 1], radius=6, fill=255)

    try:
        ss = Image.open(str(screenshot_path)).convert("RGB")
        ss = ss.resize((screen_w, screen_h), Image.LANCZOS)
        canvas.paste(ss, (bx, by), screen_mask)
    except Exception as e:  # noqa: BLE001
        log.warning("phone screenshot paste failed: %s", e)
        return None

    return canvas




def _draw_neon_glow(img: Image.Image, cx: int, cy: int, radius: int, color: tuple[int, int, int, int]):
    """Draws a soft neon radial glow behind elements for a tech aesthetic."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    # Draw concentric circles with decreasing opacity
    for r in range(radius, 0, -8):
        alpha = int((1 - (r / radius)) * color[3])
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(color[0], color[1], color[2], alpha))
    img.alpha_composite(glow)

def _draw_badge_icon(bd: ImageDraw.ImageDraw, cx: int, cy: int, r: int, accent: tuple[int, int, int]) -> None:
    """Draw a crisp vector check-mark inside an accent ring (emoji don't render
    in Pillow's bundled fonts, so we draw the glyph ourselves)."""
    bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*accent, 45), outline=(*accent, 255), width=2)
    # check mark
    s = r * 0.55
    bd.line(
        [(cx - s * 0.55, cy + s * 0.05), (cx - s * 0.1, cy + s * 0.5), (cx + s * 0.65, cy - s * 0.5)],
        fill=(255, 255, 255), width=max(3, int(r * 0.18)), joint="curve",
    )


def _draw_floating_badge(img: Image.Image, x: int, y: int, w: int, h: int, title: str, subtitle: str, accent: tuple[int, int, int]):
    """Draws a semi-transparent glassmorphic floating badge with clean borders."""
    badge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)

    # Rounded glass background + glowing border
    bd.rounded_rectangle([0, 0, w - 4, h - 4], radius=20, fill=(12, 18, 28, 225))
    bd.rounded_rectangle([0, 0, w - 4, h - 4], radius=20, outline=(*accent, 190), width=2)

    # Vector icon (drawn, not emoji)
    _draw_badge_icon(bd, cx=42, cy=h // 2, r=24, accent=accent)

    # Title & subtitle — truncate on word boundary with an ellipsis (no mid-word cuts)
    tfont, sfont = _font(20, bold=True), _font(15)

    def _fit(text: str, font, max_w: int) -> str:
        text = (text or "").strip()
        if not text or bd.textlength(text, font=font) <= max_w:
            return text
        words = text.split()
        out = ""
        for word in words:
            cand = (out + " " + word).strip()
            if bd.textlength(cand + "…", font=font) > max_w:
                break
            out = cand
        return (out + "…") if out else text

    title = _fit((title or "").upper(), tfont, w - 96)
    subtitle = _fit(subtitle, sfont, w - 96)
    bd.text((80, h // 2 - 22), title, font=tfont, fill=(255, 255, 255))
    bd.text((80, h // 2 + 4), subtitle, font=sfont, fill=(170, 188, 208))

    # Drop shadow
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x + 6, y + 8, x + w + 2, y + h + 2], radius=20, fill=(0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))

    img.alpha_composite(shadow)
    img.alpha_composite(badge, (x, y))


def _feature_badges(feature: dict, content: dict) -> list[tuple[str, str]]:
    """Build up to 4 (title, subtitle) badges from the feature's own data, so each
    feature shows its own value props — not hardcoded voice-navigation text."""
    feat = feature or {}
    keywords = [k for k in (content or {}).get("keywords", []) or feat.get("keywords", []) if k]
    uses = [u for u in feat.get("use_cases", []) if u]
    badges: list[tuple[str, str]] = []
    for i in range(4):
        title = ""
        if i < len(keywords):
            title = keywords[i]
        elif i < len(uses):
            title = " ".join(uses[i].split()[:2])
        sub = ""
        if i < len(uses):
            sub = uses[i]
        badges.append((title[:16] if title else "", sub[:26] if sub else ""))
    # Drop empty badges; guarantee at least the benefit as one badge.
    badges = [(t, s) for (t, s) in badges if t or s]
    if not badges:
        benefit = feat.get("primary_benefit") or feat.get("short") or "Trusted by 10M+ users"
        badges = [(feat.get("name", "FieldCalc")[:16], benefit[:26])]
    return badges[:4]


def _variant_for(slide: dict, feature: dict) -> int:
    forced = slide.get("forced_visual_variant")
    if forced is not None:
        try:
            return int(forced) % 5
        except (TypeError, ValueError):
            pass
    basis = f"{feature.get('id', '')}:{slide.get('render_seed_key', '')}"
    return int(hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8], 16) % 5


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
               max_w: int, max_lines: int) -> list[str]:
    lines = _wrap(draw, text, font, max_w)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_w:
        lines[-1] = " ".join(lines[-1].split()[:-1]) or lines[-1][:-1]
    lines[-1] = lines[-1].rstrip(" .,") + "…"
    return lines


def _draw_soft_panel(canvas: Image.Image, box: tuple[int, int, int, int],
                     radius: int = 34, fill: tuple[int, int, int, int] = (8, 13, 22, 210)) -> None:
    x1, y1, x2, y2 = box
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x1 + 10, y1 + 14, x2 + 10, y2 + 14], radius=radius, fill=(0, 0, 0, 95))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(box, radius=radius, fill=fill)


def _draw_brand_mark(draw: ImageDraw.ImageDraw, x: int, y: int, color=(255, 255, 255, 230)) -> None:
    draw.text((x, y), "FIELDCALC", font=_font(22, bold=True), fill=color)


def _draw_text_block(canvas: Image.Image, feature: dict, angle: dict, accent: tuple[int, int, int],
                     box: tuple[int, int, int, int], *, dark: bool = True) -> None:
    x1, y1, x2, y2 = box
    draw = ImageDraw.Draw(canvas)
    fg = (255, 255, 255) if dark else (12, 18, 28)
    muted = (205, 218, 230) if dark else (70, 88, 106)
    _draw_brand_mark(draw, x1, y1, fg)

    title = (feature.get("name") or "FieldCalc").upper()
    title_font = _font(58 if len(title) < 22 else 48, bold=True)
    ty = y1 + 52
    for line in _fit_lines(draw, title, title_font, x2 - x1, 2):
        draw.text((x1, ty), line, font=title_font, fill=fg)
        ty += int(title_font.size * 1.02)

    benefit = (feature.get("primary_benefit") or feature.get("short") or "").strip()
    body_font = _font(27, bold=True)
    ty += 16
    for line in _fit_lines(draw, benefit, body_font, x2 - x1, 3):
        draw.text((x1, ty), line, font=body_font, fill=muted)
        ty += int(body_font.size * 1.25)

    chip = angle.get("name") or "Feature"
    chip_font = _font(19, bold=True)
    chip_w = int(draw.textlength(chip, font=chip_font)) + 34
    chip_y = min(y2 - 46, ty + 24)
    draw.rounded_rectangle([x1, chip_y, x1 + chip_w, chip_y + 38], radius=18, fill=(*accent, 235))
    draw.text((x1 + 17, chip_y + 9), chip, font=chip_font, fill=(255, 255, 255))


def _draw_story_cards(canvas: Image.Image, feature: dict, content: dict, accent: tuple[int, int, int],
                      positions: list[tuple[int, int, int, int]]) -> None:
    draw = ImageDraw.Draw(canvas)
    badges = _feature_badges(feature, content)
    for (title, subtitle), box in zip(badges[:3], positions):
        x1, y1, x2, y2 = box
        _draw_soft_panel(canvas, box, radius=24, fill=(255, 255, 255, 226))
        draw.ellipse([x1 + 18, y1 + 22, x1 + 58, y1 + 62], fill=(*accent, 240))
        draw.line([(x1 + 29, y1 + 43), (x1 + 39, y1 + 53), (x1 + 52, y1 + 31)],
                  fill=(255, 255, 255), width=4)
        tfont = _font(20, bold=True)
        sfont = _font(16)
        draw.text((x1 + 74, y1 + 22), (title or "Feature").upper(), font=tfont, fill=(14, 22, 36))
        for i, line in enumerate(_fit_lines(draw, subtitle or "", sfont, x2 - x1 - 92, 2)):
            draw.text((x1 + 74, y1 + 50 + i * 20), line, font=sfont, fill=(80, 94, 110))


FEATURE_CTA: dict[str, str] = {
    "area-measurement": "Measure land faster with GPS",
    "distance-tracking": "Track distance with precision",
    "poi-markers": "Save important places instantly",
    "route-planner": "Plan multi-stop routes",
    "voice-navigation": "Navigate hands-free by voice",
    "find-routes": "Find the best route faster",
    "speedometer": "Track speed in real time",
    "compass": "Find direction anywhere",
    "gps-camera": "Capture proof with GPS stamp",
    "gps-gallery": "Find photos by place and time",
    "wonder-places": "Discover famous places",
    "street-view": "Preview places before you go",
    "groups": "Organize fields and projects",
    "saved-measurements": "Keep every measurement saved",
    "nearby-location": "Find nearby and share location",
}


def _draw_path_overlay(canvas: Image.Image, feature_id: str, accent: tuple[int, int, int],
                       variant: int) -> None:
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    white = (255, 255, 255, 245)
    green = (*accent, 230)

    if feature_id in {"area-measurement", "groups", "saved-measurements"}:
        pts = [(175, 470), (370, 410), (620, 450), (700, 650), (260, 690)]
        if variant in {1, 4}:
            pts = [(165, 300), (390, 260), (710, 330), (640, 540), (245, 560)]
        for x in range(155, 705, 52):
            d.line([(x, min(p[1] for p in pts) + 20), (x + 50, max(p[1] for p in pts) - 20)],
                   fill=(255, 255, 255, 45), width=1)
        d.polygon(pts, fill=(*accent, 48), outline=None)
        d.line(pts + [pts[0]], fill=white, width=7, joint="curve")
        d.line(pts + [pts[0]], fill=green, width=3, joint="curve")
        for x, y in pts:
            d.ellipse([x - 14, y - 14, x + 14, y + 14], fill=white)
            d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=green)
        label = "AREA\n2.35\nACRE"
        d.multiline_text((430, 545), label, font=_font(26, bold=True), fill=white,
                         anchor="mm", align="center", spacing=0)
    elif feature_id in {"distance-tracking", "route-planner", "find-routes", "voice-navigation"}:
        pts = [(125, 680), (275, 555), (430, 610), (605, 430), (790, 510), (940, 350)]
        if variant == 2:
            pts = [(100, 470), (260, 420), (430, 505), (610, 350), (770, 430), (950, 280)]
        d.line(pts, fill=(255, 255, 255, 235), width=12, joint="curve")
        d.line(pts, fill=green, width=6, joint="curve")
        for i, (x, y) in enumerate(pts):
            r = 16 if i in {0, len(pts) - 1} else 11
            d.ellipse([x - r, y - r, x + r, y + r], fill=white)
            d.ellipse([x - r + 5, y - r + 5, x + r - 5, y + r - 5], fill=green)
    elif feature_id in {"poi-markers", "nearby-location", "street-view", "wonder-places"}:
        for i, (x, y, label) in enumerate([(235, 430, "Work"), (520, 330, "Stop"), (760, 575, "Saved")]):
            d.rounded_rectangle([x + 18, y - 22, x + 138, y + 22], radius=14,
                                fill=(8, 13, 22, 210), outline=white, width=1)
            d.text((x + 78, y), label, font=_font(16, bold=True), fill=white, anchor="mm")
            d.ellipse([x - 17, y - 17, x + 17, y + 17], fill=green, outline=white, width=3)
            d.polygon([(x, y + 30), (x - 10, y + 12), (x + 10, y + 12)], fill=green)
    elif feature_id == "speedometer":
        cx, cy, r = 540, 510, 160
        d.arc([cx - r, cy - r, cx + r, cy + r], 205, 335, fill=white, width=16)
        d.arc([cx - r, cy - r, cx + r, cy + r], 205, 292, fill=green, width=16)
        d.line([(cx, cy), (cx + 88, cy - 76)], fill=white, width=8)
        d.text((cx, cy + 48), "GPS SPEED", font=_font(24, bold=True), fill=white, anchor="mm")
    elif feature_id == "compass":
        cx, cy, r = 540, 470, 135
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=white, width=6)
        d.polygon([(cx, cy - 110), (cx - 30, cy + 15), (cx, cy - 5), (cx + 30, cy + 15)],
                  fill=green, outline=white)
        d.text((cx, cy - 160), "N", font=_font(34, bold=True), fill=white, anchor="mm")

    canvas.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(0.2)))


def _draw_app_icon_tile(canvas: Image.Image, icon_path: str | None, x: int, y: int, size: int) -> None:
    d = ImageDraw.Draw(canvas)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x + 8, y + 10, x + size + 8, y + size + 10], radius=24, fill=(0, 0, 0, 60))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
    d.rounded_rectangle([x, y, x + size, y + size], radius=24, fill=(255, 255, 255, 255))
    if icon_path and Path(str(icon_path)).exists():
        try:
            icon = Image.open(str(icon_path)).convert("RGBA")
            icon.thumbnail((size - 14, size - 14), Image.LANCZOS)
            ix = x + (size - icon.width) // 2
            iy = y + (size - icon.height) // 2
            canvas.alpha_composite(icon, (ix, iy))
            return
        except Exception as e:  # noqa: BLE001
            log.warning("app icon paste failed: %s", e)
    d.text((x + size // 2, y + size // 2), "FC", font=_font(28, bold=True),
           fill=(12, 18, 28), anchor="mm")


def _draw_curved_footer(canvas: Image.Image, slide: dict, feature: dict, angle: dict,
                        accent: tuple[int, int, int], *, compact: bool = False) -> None:
    W, H = canvas.size
    footer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(footer)
    top = 700 if not compact else 735
    d.ellipse([-180, top - 180, W + 220, H + 380], fill=(255, 255, 255, 248))
    d.pieslice([-220, top - 230, W + 220, H + 120], 180, 360, fill=(255, 255, 255, 248))
    d.line([(0, top + 15), (W, top - 55)], fill=(*accent, 245), width=14)
    canvas.alpha_composite(footer)

    draw = ImageDraw.Draw(canvas)
    icon_size = 110 if not compact else 86
    _draw_app_icon_tile(canvas, slide.get("app_icon_path"), 66, 792 if not compact else 820, icon_size)

    title = (feature.get("name") or "GPS Area Measure").strip()
    app_name = "FieldCalc -"
    x = 205 if not compact else 175
    y = 792 if not compact else 816
    draw.text((x, y), app_name, font=_font(54 if not compact else 42, bold=True), fill=accent)
    for i, line in enumerate(_fit_lines(draw, title, _font(50 if not compact else 38, bold=True), 770, 2)):
        draw.text((x, y + 62 + i * (54 if not compact else 42)),
                  line, font=_font(50 if not compact else 38, bold=True), fill=(5, 18, 36))
    draw.line([(x, y + 128), (x + 170, y + 128)], fill=accent, width=4)

    cta = FEATURE_CTA.get(feature.get("id", ""), feature.get("primary_benefit") or "Use GPS smarter")
    chip_y = 920 if not compact else 930
    chip_w = min(760, int(draw.textlength(cta, font=_font(31, bold=True))) + 76)
    draw.rounded_rectangle([66, chip_y, 66 + chip_w, chip_y + 64], radius=18, fill=accent)
    draw.text((105, chip_y + 32), cta, font=_font(31, bold=True), fill=(255, 255, 255), anchor="lm")

    if str(slide.get("show_10m", "")).lower() in {"1", "true", "yes"} or not compact:
        text = "10M+ users"
        tw = int(draw.textlength(text, font=_font(32, bold=True))) + 78
        x2 = min(66 + tw, 410)
        y2 = chip_y + 70
        draw.rounded_rectangle([66, y2, x2, y2 + 58], radius=16, fill=(255, 255, 255, 245),
                               outline=(*accent, 230), width=2)
        draw.text((112, y2 + 29), text, font=_font(32, bold=True), fill=(10, 20, 34), anchor="lm")


def _use_phone_for_variant(slide: dict, feature_id: str, variant: int) -> bool:
    if variant not in {0, 3}:
        return False
    if feature_id not in {
        "area-measurement", "distance-tracking", "route-planner", "poi-markers",
        "find-routes", "gps-camera", "saved-measurements", "groups",
    }:
        return False
    path = slide.get("screenshot_path")
    return bool(path and Path(str(path)).exists())

# ---------------------------------------------------------------------------
# Main composite builder
# ---------------------------------------------------------------------------
def create_marketing_composite(img_bytes: bytes, slide: dict, feature: dict | None = None, content: dict | None = None) -> Image.Image:
    W, H = 1080, 1080
    accent = _rgb(slide.get("accent", "#0aa77f"))
    feature = feature or {}
    angle = {"name": slide.get("eyebrow") or ""}
    feature_id = feature.get("id", "")
    variant = _variant_for(slide, feature)

    # Load and scale full-bleed AI background
    bg = Image.open(io.BytesIO(img_bytes)).convert("RGBA").resize((W, H), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(bg, (0, 0))

    # Scene-first poster system. The photo carries the emotion; drawn overlays
    # explain the feature. No bottom Google Play button and no fake phone UI.
    if variant == 0:
        # Reference-style hero: real scenario + measurement overlay + optional real screenshot phone.
        gradient = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gradient)
        for y in range(0, 760):
            alpha = int(95 * (1 - y / 760))
            gd.line([(0, y), (W, y)], fill=(3, 8, 15, alpha))
        canvas.alpha_composite(gradient)
        draw = ImageDraw.Draw(canvas)
        label = angle.get("name") or "Feature"
        draw.rounded_rectangle([62, 58, 62 + int(draw.textlength(label, font=_font(27, bold=True))) + 42, 112],
                               radius=18, fill=(*accent, 245))
        draw.text((83, 85), label, font=_font(27, bold=True), fill=(255, 255, 255), anchor="lm")
        _draw_path_overlay(canvas, feature_id, accent, variant)
        _draw_curved_footer(canvas, slide, feature, angle, accent)
        if _use_phone_for_variant(slide, feature_id, variant):
            phone = _draw_phone_mockup(slide.get("screenshot_path", ""), accent, 286, 572)
            if phone:
                canvas.alpha_composite(phone, (720, 230))
    elif variant == 1:
        # Editorial real-use poster, no mockup.
        canvas.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 44)))
        _draw_path_overlay(canvas, feature_id, accent, variant)
        _draw_soft_panel(canvas, (62, 626, 1018, 1014), radius=38, fill=(255, 255, 255, 242))
        _draw_text_block(canvas, feature, angle, accent, (108, 672, 935, 940), dark=False)
        _draw_story_cards(canvas, feature, content or {}, accent, [(70, 78, 455, 178), (70, 198, 505, 298)])
    elif variant == 2:
        # Field-note style: proof overlay cards, no phone.
        canvas.alpha_composite(Image.new("RGBA", (W, H), (3, 8, 14, 72)))
        _draw_path_overlay(canvas, feature_id, accent, variant)
        _draw_soft_panel(canvas, (58, 58, 1022, 360), radius=36, fill=(8, 13, 22, 214))
        _draw_text_block(canvas, feature, angle, accent, (100, 98, 940, 320), dark=True)
        _draw_story_cards(canvas, feature, content or {}, accent,
                          [(70, 700, 430, 800), (455, 754, 815, 854), (650, 878, 1010, 978)])
    elif variant == 3:
        # Split proof: compact text + optional Android proof on the side.
        canvas.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 82)))
        _draw_path_overlay(canvas, feature_id, accent, variant)
        _draw_soft_panel(canvas, (58, 70, 625, 570), radius=38, fill=(8, 13, 22, 222))
        _draw_text_block(canvas, feature, angle, accent, (98, 116, 570, 510), dark=True)
        if not _use_phone_for_variant(slide, feature_id, variant):
            _draw_story_cards(canvas, feature, content or {}, accent, [(682, 236, 1010, 336), (682, 358, 1010, 458)])
        _draw_curved_footer(canvas, slide, feature, angle, accent, compact=True)
        if _use_phone_for_variant(slide, feature_id, variant):
            phone = _draw_phone_mockup(slide.get("screenshot_path", ""), accent, 282, 564)
            if phone:
                canvas.alpha_composite(phone, (720, 226))
    else:
        # Premium minimal: big realistic scene and concise brand system.
        canvas.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 48)))
        _draw_path_overlay(canvas, feature_id, accent, variant)
        _draw_curved_footer(canvas, slide, feature, angle, accent, compact=True)
        draw = ImageDraw.Draw(canvas)
        label = angle.get("name") or "Real Use"
        draw.rounded_rectangle([62, 62, 62 + int(draw.textlength(label, font=_font(24, bold=True))) + 40, 112],
                               radius=18, fill=(8, 13, 22, 210), outline=(*accent, 220), width=2)
        draw.text((82, 87), label, font=_font(24, bold=True), fill=(255, 255, 255), anchor="lm")

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
        final = create_marketing_composite(img_bytes, slide, feature=feature, content=content or {})
        out_path = OUT_DIR / f"{post_key}_ai.png"
        final.save(out_path)
        log.info("marketing composite saved: %s", out_path)
        return str(out_path)
    except Exception as e:  # noqa: BLE001
        log.warning("generate_realistic_post failed for %s: %s", post_key, e)
        return None
