"""Image generation — format-aware, HTML-first.

generate_assets() builds a slide spec from the feature/angle/caption, then renders
each slide via the PRIMARY engine (HTML/CSS -> Chromium screenshot) and falls back
to the Pillow card generator when Chromium isn't available. Either way it returns a
list of local PNG paths (1 for SINGLE/STORY, N for CAROUSEL) and NEVER fails.

Design choice (C6): obviously-branded marketing graphics — gradients, big type, app
chip — not photorealistic AI scenery, to stay clear of Meta's AI-image label triggers.
"""
from __future__ import annotations

import json
import logging
import base64
import hashlib
import math
import mimetypes
import textwrap
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app import render_html
from app.config import get_settings
from app.database import PostFormat

log = logging.getLogger("autopost")
settings = get_settings()

OUT_DIR = Path(__file__).resolve().parent.parent / "generated"
OUT_DIR.mkdir(exist_ok=True)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SIZES = {
    PostFormat.SINGLE: (1080, 1080),
    PostFormat.CAROUSEL: (1080, 1350),
    PostFormat.STORY: (1080, 1920),
}

# Feature icon name -> emoji (cheap, license-free visual identity per feature).
ICONS = {
    "ruler": "📐", "route": "📏", "map-pin": "📍", "list-checks": "🗺️",
    "mic": "🎙️", "navigation": "🚗", "gauge": "🏎️", "compass": "🧭",
    "camera": "📷", "images": "🖼️", "globe": "🌍", "panorama": "🏙️",
    "folder": "📁", "save": "💾", "locate": "📡",
}

BRAND_BG = (10, 14, 20)
BRAND_ACCENT = (232, 99, 74)
BRAND_TEXT = (242, 245, 248)
BRAND_MUTED = (154, 166, 178)


def _existing_asset_url(path: str | None) -> str:
    if not path:
        return ""
    p = (PROJECT_ROOT / path).resolve()
    if not p.exists() or p.suffix.lower() == ".xml":
        return ""
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"
    except Exception:
        return p.as_uri()


def _existing_asset_path(path: str | None) -> str:
    if not path:
        return ""
    p = (PROJECT_ROOT / path).resolve()
    if not p.exists() or p.suffix.lower() == ".xml":
        return ""
    return str(p)


def _asset_path_from_url(url: str | None) -> Path | None:
    if not url or not url.startswith("file://"):
        return None
    try:
        from urllib.parse import unquote, urlparse

        p = Path(unquote(urlparse(url).path))
        return p if p.exists() else None
    except Exception:
        return None


def _load_visual_assets() -> dict:
    try:
        return json.loads((DATA_DIR / "visual_assets.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - visuals should never break the daily post
        log.warning("visual asset manifest unavailable: %s", e)
        return {"defaults": {}, "features": {}}


def _visuals_for_feature(feature: dict) -> dict:
    manifest = _load_visual_assets()
    defaults = manifest.get("defaults", {})
    brand = defaults.get("brand", {})
    features = manifest.get("features", {})
    feature_visuals = features.get(feature.get("id", ""), {})
    default_screens = defaults.get("screenshots", [])
    screenshot = feature_visuals.get("screenshot") or (default_screens[0] if default_screens else "")
    realistic_asset_path = _existing_asset_path(feature_visuals.get("realistic_asset"))
    scene_asset_path = _existing_asset_path(feature_visuals.get("scene_asset")) or realistic_asset_path
    return {
        "app_icon_url": _existing_asset_url(defaults.get("app_icon")),
        "app_icon_path": _existing_asset_path(defaults.get("app_icon")),
        "screenshot_url": _existing_asset_url(screenshot),
        "screenshot_path": _existing_asset_path(screenshot),
        "realistic_asset_path": realistic_asset_path,
        "scene_asset_path": scene_asset_path,
        "feature_icon_url": _existing_asset_url(feature_visuals.get("feature_icon")),
        "accent": feature_visuals.get("accent") or "#e8634a",
        "scenario": feature_visuals.get("scenario") or "field_measurement",
        "show_satellite": bool(feature_visuals.get("show_satellite", False)),
        "publisher": brand.get("publisher") or "Vasundhara Infotech LLP",
        "rating": brand.get("rating") or "4.1",
        "downloads": brand.get("downloads") or "10M+",
        "cta_text": brand.get("cta") or "Install Free",
    }


def has_realistic_asset(feature: dict) -> bool:
    """True when this feature has a curated live-ready realistic visual."""
    return bool(_visuals_for_feature(feature).get("realistic_asset_path"))


def _title_lines(title: str, max_chars: int = 16) -> list[str]:
    words = title.split()
    if len(title) <= max_chars or len(words) <= 1:
        return [title]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > max_chars:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    if len(lines) > 2:
        return [" ".join(lines[:-1]), lines[-1]]
    return lines


# --------------------------------------------------------------------------
# Slide spec — what each image should say (shared by HTML + Pillow renderers)
# --------------------------------------------------------------------------
def _build_slides(
    feature: dict,
    angle: dict,
    fmt: PostFormat,
    content: Optional[dict],
    strategy: Optional[dict] = None,
) -> List[dict]:
    app = settings.app_name
    icon = ICONS.get(feature.get("icon", ""), "📍")
    visuals = _visuals_for_feature(feature)
    strategy = strategy or {}
    if strategy:
        visuals.update({
            "creative_image_type": strategy.get("image_type_id", ""),
            "creative_image_label": strategy.get("image_type_label", ""),
            "country_name": strategy.get("country_name", ""),
            "logo_rule": strategy.get("logo_rule", "small_or_none"),
            "show_10m": strategy.get("show_10m", False),
            "uses_app_mockup": strategy.get("uses_app_mockup", False),
            "forced_visual_variant": strategy.get("visual_variant"),
        })
    benefit = feature.get("primary_benefit", "")
    short = feature.get("short", "")
    cta = (content or {}).get("cta") or angle.get("cta_style", "Try it free →")
    chips = [k.title() for k in (content or {}).get("keywords", [])][:3]
    use_cases = feature.get("use_cases", [])[:3]
    audience = ", ".join(feature.get("audiences", [])[:2])

    angle_id = angle.get("id", "")

    if fmt == PostFormat.SINGLE:
        return [dict(kind="single", w=1080, h=1080, app_name=app, eyebrow=angle["name"],
                     icon=icon, title=feature["name"], subtitle=benefit or short,
                     title_lines=_title_lines(feature["name"]),
                     footer=cta, chips=chips, visual_mode="hero", audience=audience,
                     angle_id=angle_id, feature_id=feature.get("id", ""),
                     caption_text=(content or {}).get("caption", ""), **visuals)]

    if fmt == PostFormat.STORY:
        return [dict(kind="story", w=1080, h=1920, app_name=app, eyebrow=angle["name"],
                     icon=icon, title=feature["name"], subtitle=benefit or short,
                     title_lines=_title_lines(feature["name"], 18),
                     footer="Get FieldCalc free", visual_mode="story", audience=audience,
                     angle_id=angle_id, feature_id=feature.get("id", ""),
                     caption_text=(content or {}).get("caption", ""), **visuals)]

    # CAROUSEL: hook -> what -> why -> cta
    w, h = SIZES[PostFormat.CAROUSEL]
    slides = [
        dict(kind="hook", title=feature["name"], subtitle=benefit, icon=icon,
             eyebrow=angle["name"], footer="Swipe →", visual_mode="hero"),
        dict(kind="point", title="How it works", subtitle=short,
             bullets=use_cases or None, footer="Swipe →", visual_mode="split"),
        dict(kind="point", title="Why it matters", subtitle=benefit,
             chips=chips or None, footer="Swipe →", visual_mode="proof"),
        dict(kind="cta", title="Get FieldCalc", subtitle=cta, footer="Link in bio →", visual_mode="cta"),
    ]
    total = len(slides)
    angle_id = angle.get("id", "")
    for i, s in enumerate(slides):
        s.update(w=w, h=h, app_name=app, index=i, total=total, audience=audience,
                 angle_id=angle_id, feature_id=feature.get("id", ""),
                 caption_text=(content or {}).get("caption", ""), **visuals)
        s["title_lines"] = _title_lines(s.get("title", ""), 16)
        s.setdefault("eyebrow", "")
    return slides


# --------------------------------------------------------------------------
# Pillow fallback renderer (guaranteed to work with no browser)
# --------------------------------------------------------------------------
def _font(size: int, bold: bool = False):
    for c in [
        f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
    ]:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rgb(value: str | tuple[int, int, int], fallback=(10, 167, 127)) -> tuple[int, int, int]:
    if isinstance(value, tuple):
        return value[:3]
    try:
        raw = str(value or "").strip().lstrip("#")
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _cover_image(src: Image.Image, size: tuple[int, int], y_bias: float = 0.5) -> Image.Image:
    src = src.convert("RGB")
    sw, sh = src.size
    tw, th = size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    src = src.resize((nw, nh), Image.Resampling.LANCZOS)
    crop_x = (nw - tw) // 2
    crop_y = int(max(0, nh - th) * max(0, min(1, y_bias)))
    return src.crop((crop_x, crop_y, crop_x + tw, crop_y + th))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
    return mask


def _paste_round(base: Image.Image, src: Image.Image, xy: tuple[int, int], size: tuple[int, int], radius: int, y_bias: float = 0.5) -> None:
    src = _cover_image(src, size, y_bias=y_bias)
    base.paste(src, xy, _rounded_mask(size, radius))


def _shadow(base: Image.Image, box: tuple[int, int, int, int], radius: int, opacity: int = 70, blur: int = 22) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, fill=(0, 0, 0, opacity))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)


def _wrap_lines(text: str, font, max_width: int, max_lines: int = 4) -> list[str]:
    if max_lines <= 0:
        return []
    words = str(text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for word in words:
        candidate = " ".join([*current, word])
        if current and probe.textlength(candidate, font=font) > max_width:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) >= max_lines:
                break
        else:
            current.append(word)
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(".") + "..."
    return lines


def _draw_gradient(img: Image.Image, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    d = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=_mix(top, bottom, t))


def _slide_asset_path(slide: dict, key: str) -> Path | None:
    value = slide.get(key)
    if value:
        p = Path(value)
        if p.exists() and p.suffix.lower() != ".xml":
            return p
    return None


def _draw_photo_backdrop(
    img: Image.Image,
    path: Path,
    box: tuple[int, int, int, int],
    accent: tuple[int, int, int],
    *,
    y_bias: float = 0.32,
    blur: float = 1.0,
    dim: int = 52,
    tint: int = 42,
) -> bool:
    try:
        x, y, w, h = box
        photo = Image.open(path).convert("RGB")
        photo = _cover_image(photo, (w, h), y_bias=y_bias)
        photo = ImageEnhance.Color(photo).enhance(1.12)
        photo = ImageEnhance.Contrast(photo).enhance(1.10)
        if blur:
            photo = photo.filter(ImageFilter.GaussianBlur(blur))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        layer.paste(photo.convert("RGBA"), (x, y), _rounded_mask((w, h), 36))
        overlay = Image.new("RGBA", (w, h), (*_mix(accent, (255, 255, 255), .68), tint))
        dark = Image.new("RGBA", (w, h), (8, 13, 20, dim))
        layer.alpha_composite(overlay, (x, y))
        layer.alpha_composite(dark, (x, y))
        img.alpha_composite(layer)
        return True
    except Exception:
        return False


def _draw_app_icon(img: Image.Image, slide: dict, x: int, y: int, size: int) -> None:
    icon_path = Path(slide["app_icon_path"]) if slide.get("app_icon_path") else _asset_path_from_url(slide.get("app_icon_url"))
    d = ImageDraw.Draw(img)
    _shadow(img, (x + 4, y + 8, x + size + 4, y + size + 8), max(12, size // 4), 55, 16)
    d.rounded_rectangle([x, y, x + size, y + size], radius=max(12, size // 4), fill=(255, 255, 255, 255))
    if not icon_path:
        d.text((x + size * 0.27, y + size * 0.23), "FC", font=_font(size // 3, True), fill=(12, 25, 44))
        return
    try:
        icon = Image.open(icon_path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        img.paste(icon, (x, y), _rounded_mask((size, size), max(12, size // 4)))
    except Exception:
        d.text((x + size * 0.27, y + size * 0.23), "FC", font=_font(size // 3, True), fill=(12, 25, 44))


def _draw_phone(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    x, y, pw, ph = box
    d = ImageDraw.Draw(img)
    radius = max(32, pw // 7)
    _shadow(img, (x + 14, y + 22, x + pw + 14, y + ph + 22), radius, 95, 28)
    d.rounded_rectangle([x, y, x + pw, y + ph], radius=radius, fill=(13, 20, 31, 255))
    d.rounded_rectangle([x + 8, y + 8, x + pw - 8, y + ph - 8], radius=radius - 8, fill=(255, 255, 255, 255))
    screen = (x + 18, y + 34, pw - 36, ph - 58)
    shot_path = Path(slide["screenshot_path"]) if slide.get("screenshot_path") else _asset_path_from_url(slide.get("screenshot_url"))
    if shot_path:
        try:
            shot = Image.open(shot_path).convert("RGB")
            _paste_round(img, shot, (screen[0], screen[1]), (screen[2], screen[3]), max(24, pw // 12), y_bias=0.0)
        except Exception:
            shot_path = None
    if not shot_path:
        d.rounded_rectangle([screen[0], screen[1], screen[0] + screen[2], screen[1] + screen[3]],
                            radius=max(24, pw // 12), fill=(238, 244, 248, 255))
        d.rectangle([screen[0], screen[1], screen[0] + screen[2], screen[1] + 58], fill=accent)
        d.text((screen[0] + 22, screen[1] + 18), slide.get("title", "Feature"), font=_font(22, True), fill=(255, 255, 255))
    notch_w, notch_h = int(pw * 0.28), max(14, int(ph * 0.025))
    d.rounded_rectangle([x + (pw - notch_w) // 2, y + 8, x + (pw + notch_w) // 2, y + 8 + notch_h],
                        radius=notch_h // 2, fill=(13, 20, 31, 255))
    d.rounded_rectangle([x + 26, y + ph - 28, x + pw - 26, y + ph - 16], radius=6, fill=(22, 31, 44, 255))


def _draw_person(img: Image.Image, x: int, y: int, scale: float, accent: tuple[int, int, int], hard_hat: bool = False) -> None:
    d = ImageDraw.Draw(img)
    s = scale
    skin = (174, 112, 74)
    shirt = _mix(accent, (20, 30, 40), 0.18)
    d.ellipse([x + int(38*s), y, x + int(82*s), y + int(44*s)], fill=skin)
    if hard_hat:
        d.pieslice([x + int(30*s), y - int(10*s), x + int(90*s), y + int(32*s)], 180, 360, fill=(245, 216, 94))
        d.rectangle([x + int(28*s), y + int(18*s), x + int(92*s), y + int(28*s)], fill=(245, 216, 94))
    d.rounded_rectangle([x + int(22*s), y + int(48*s), x + int(98*s), y + int(162*s)], radius=int(22*s), fill=shirt)
    d.polygon([(x + int(22*s), y + int(72*s)), (x - int(10*s), y + int(138*s)), (x + int(16*s), y + int(148*s)), (x + int(52*s), y + int(92*s))], fill=skin)
    d.polygon([(x + int(96*s), y + int(72*s)), (x + int(142*s), y + int(126*s)), (x + int(122*s), y + int(146*s)), (x + int(72*s), y + int(96*s))], fill=skin)
    d.rounded_rectangle([x + int(110*s), y + int(112*s), x + int(154*s), y + int(176*s)], radius=int(8*s), fill=(18, 28, 42))
    d.rounded_rectangle([x + int(116*s), y + int(118*s), x + int(148*s), y + int(168*s)], radius=int(5*s), fill=(225, 239, 235))
    d.polygon([(x + int(34*s), y + int(160*s)), (x + int(62*s), y + int(160*s)), (x + int(50*s), y + int(244*s)), (x + int(22*s), y + int(244*s))], fill=(31, 45, 65))
    d.polygon([(x + int(72*s), y + int(160*s)), (x + int(98*s), y + int(160*s)), (x + int(120*s), y + int(244*s)), (x + int(90*s), y + int(244*s))], fill=(31, 45, 65))


def _draw_isometric_tile(d: ImageDraw.ImageDraw, cx: int, cy: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    top = [(cx, cy - h // 2), (cx + w // 2, cy), (cx, cy + h // 2), (cx - w // 2, cy)]
    side = [(cx - w // 2, cy), (cx, cy + h // 2), (cx, cy + h // 2 + 28), (cx - w // 2, cy + 28)]
    side2 = [(cx + w // 2, cy), (cx, cy + h // 2), (cx, cy + h // 2 + 28), (cx + w // 2, cy + 28)]
    d.polygon(side, fill=_mix(color, (0, 0, 0), 0.22))
    d.polygon(side2, fill=_mix(color, (255, 255, 255), 0.10))
    d.polygon(top, fill=color)
    d.line(top + [top[0]], fill=(255, 255, 255), width=3)


def _draw_context_backdrop(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int], opacity: int = 155) -> bool:
    """Use the real feature screenshot as a soft scene texture when it helps."""
    shot_path = _slide_asset_path(slide, "scene_asset_path")
    if not shot_path:
        shot_path = Path(slide["screenshot_path"]) if slide.get("screenshot_path") else _asset_path_from_url(slide.get("screenshot_url"))
    if not shot_path:
        return False
    try:
        x, y, w, h = box
        shot = Image.open(shot_path).convert("RGB")
        shot = _cover_image(shot, (w, h), y_bias=0.18)
        shot = ImageEnhance.Color(shot).enhance(1.08)
        shot = ImageEnhance.Contrast(shot).enhance(1.08)
        shot = shot.filter(ImageFilter.GaussianBlur(0.7))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        layer.paste(shot.convert("RGBA"), (x, y), _rounded_mask((w, h), 36))
        wash = Image.new("RGBA", (w, h), (*_mix(accent, (255, 255, 255), .72), max(55, 210 - opacity)))
        layer.alpha_composite(wash, (x, y))
        alpha = layer.getchannel("A").point(lambda p: min(p, opacity))
        layer.putalpha(alpha)
        img.alpha_composite(layer)
        return True
    except Exception:
        return False


def _draw_photo_hero_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    """A stronger marketing-poster layout driven by real app visual assets."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    _draw_gradient(img, (232, 241, 247), _mix(accent, (255, 255, 255), .83))
    hero_box = (42, 48, w - 84, int(h * (.62 if h <= w else .56)))
    scene_asset = _slide_asset_path(slide, "scene_asset_path")
    if scene_asset:
        _draw_photo_backdrop(img, scene_asset, hero_box, accent, y_bias=(seed % 7) / 10, blur=0.35, dim=38, tint=30)
    else:
        _draw_feature_scene(img, slide, hero_box, accent, seed)

    # Glass wash makes uncontrolled screenshot text recede while the designed
    # feature overlay and phone stay sharp.
    wash = Image.new("RGBA", img.size, (0, 0, 0, 0))
    wd = ImageDraw.Draw(wash)
    wd.rounded_rectangle(
        [hero_box[0], hero_box[1], hero_box[0] + hero_box[2], hero_box[1] + hero_box[3]],
        radius=36,
        fill=(255, 255, 255, 70),
    )
    img.alpha_composite(wash)
    _draw_feature_symbol_overlay(img, slide, hero_box, accent)

    if slide.get("screenshot_path") or slide.get("screenshot_url"):
        phone_variant = _seed_variant(slide.get("render_seed_key", ""), seed, 2)
        pw = int(w * (.30 if phone_variant == 0 else .34))
        ph = int(min(h * .55, pw * 1.78))
        px = int(w * (.62 if phone_variant == 0 else .57))
        py = int(h * (.10 if h <= w else .12))
        _draw_phone(img, slide, (px, py, pw, ph), accent)

    if slide.get("feature_id") in {
        "area-measurement", "distance-tracking", "poi-markers", "route-planner",
        "find-routes", "nearby-location", "groups", "saved-measurements",
    }:
        _draw_person(
            img,
            hero_box[0] + int(hero_box[2] * .05),
            hero_box[1] + int(hero_box[3] * .45),
            max(.62, hero_box[2] / 1150),
            accent,
            hard_hat=slide.get("feature_id") != "area-measurement",
        )

    panel_h = int(h * (.28 if h <= w else .30))
    panel_y = h - panel_h - 58
    _draw_text_panel(img, slide, (58, panel_y, w - 116, panel_h), accent, compact=h <= w)


def _draw_measure_overlay(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], accent: tuple[int, int, int], mode: str) -> None:
    x, y, w, h = box
    if mode in {"distance-tracking", "route-planner", "find-routes", "voice-navigation", "nearby-location"}:
        pts = [(x + int(w * .12), y + int(h * .70)), (x + int(w * .35), y + int(h * .45)),
               (x + int(w * .56), y + int(h * .58)), (x + int(w * .83), y + int(h * .30))]
        d.line(pts, fill=(255, 255, 255), width=13, joint="curve")
        d.line(pts, fill=accent, width=7, joint="curve")
        for i, (px, py) in enumerate(pts):
            d.ellipse([px - 18, py - 18, px + 18, py + 18], fill=(255, 255, 255))
            d.ellipse([px - 10, py - 10, px + 10, py + 10], fill=accent)
            if i in {1, 2}:
                label = f"{80 + i * 23}.4 m"
                d.rounded_rectangle([px + 16, py - 48, px + 126, py - 10], radius=12, fill=(12, 18, 28))
                d.text((px + 28, py - 42), label, font=_font(20, True), fill=(255, 255, 255))
        return
    pts = [(x + int(w * .18), y + int(h * .62)), (x + int(w * .36), y + int(h * .35)),
           (x + int(w * .70), y + int(h * .42)), (x + int(w * .82), y + int(h * .72)),
           (x + int(w * .42), y + int(h * .78))]
    fill = _mix(accent, (255, 255, 255), 0.58)
    d.polygon(pts, fill=fill)
    d.line(pts + [pts[0]], fill=(255, 255, 255), width=9)
    d.line(pts + [pts[0]], fill=accent, width=4)
    for px, py in pts:
        d.ellipse([px - 16, py - 16, px + 16, py + 16], fill=(255, 255, 255))
        d.ellipse([px - 8, py - 8, px + 8, py + 8], fill=accent)
    if mode == "area-measurement":
        d.text((x + int(w * .43), y + int(h * .53)), "2.35", font=_font(48, True), fill=(255, 255, 255))
        d.text((x + int(w * .46), y + int(h * .64)), "ACRE", font=_font(22, True), fill=(255, 255, 255))


def _draw_map_pin(d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, fill: tuple[int, int, int], label: str | None = None) -> None:
    d.ellipse([cx - size // 2, cy - size, cx + size // 2, cy], fill=fill, outline=(255, 255, 255), width=max(3, size // 10))
    d.polygon([(cx - size // 3, cy - size // 3), (cx + size // 3, cy - size // 3), (cx, cy + size // 3)], fill=fill)
    d.ellipse([cx - size // 8, cy - size * 3 // 4, cx + size // 8, cy - size // 2], fill=(255, 255, 255))
    if label:
        font = _font(max(16, size // 3), True)
        tw = int(d.textlength(label, font=font))
        d.rounded_rectangle([cx + size // 3, cy - size, cx + size // 3 + tw + 28, cy - size + 36], radius=14, fill=(255, 255, 255, 235))
        d.text((cx + size // 3 + 14, cy - size + 8), label, font=font, fill=(12, 25, 44))


def _draw_record_stack(img: Image.Image, box: tuple[int, int, int, int], accent: tuple[int, int, int], title: str = "Saved") -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    card_w = int(w * .42)
    card_h = int(h * .18)
    for i, label in enumerate([title, "Field A", "Boundary"]):
        cx = x + int(w * (.12 + i * .10))
        cy = y + int(h * (.30 + i * .13))
        _shadow(img, (cx + 8, cy + 10, cx + card_w + 8, cy + card_h + 10), 24, 36, 12)
        d.rounded_rectangle([cx, cy, cx + card_w, cy + card_h], radius=24, fill=(255, 255, 255, 240))
        d.rounded_rectangle([cx + 22, cy + 22, cx + 78, cy + card_h - 22], radius=14, fill=_mix(accent, (255, 255, 255), .38))
        d.text((cx + 100, cy + 26), label, font=_font(24, True), fill=(12, 25, 44))
        d.text((cx + 100, cy + 62), "Area | Distance | Map", font=_font(18, True), fill=(88, 105, 123))


def _draw_voice_waves(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    x, y, w, h = box
    cx, cy = x + int(w * .35), y + int(h * .52)
    d.rounded_rectangle([cx - 48, cy - 92, cx + 48, cy + 70], radius=40, fill=accent, outline=(255, 255, 255), width=7)
    d.rectangle([cx - 14, cy + 66, cx + 14, cy + 130], fill=(255, 255, 255))
    d.arc([cx - 95, cy - 18, cx + 95, cy + 152], 20, 160, fill=(255, 255, 255), width=8)
    d.line([(cx - 70, cy + 132), (cx + 70, cy + 132)], fill=(255, 255, 255), width=8)
    for i in range(4):
        r = 82 + i * 38
        d.arc([cx - r, cy - r, cx + r, cy + r], 310, 50, fill=(*accent, 255), width=6)


def _draw_itinerary_cards(img: Image.Image, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    labels = ["Start", "Stop 1", "Stop 2", "Done"]
    x0 = x + int(w * .11)
    for i, label in enumerate(labels):
        cy = y + int(h * (.18 + i * .17))
        d.line([(x0 + 18, y + int(h * .18)), (x0 + 18, y + int(h * .70))], fill=(255, 255, 255), width=6)
        d.ellipse([x0, cy - 18, x0 + 36, cy + 18], fill=accent, outline=(255, 255, 255), width=4)
        d.rounded_rectangle([x0 + 58, cy - 30, x0 + int(w * .54), cy + 30], radius=18, fill=(255, 255, 255, 232))
        d.text((x0 + 82, cy - 14), label, font=_font(22, True), fill=(12, 25, 44))


def _draw_feature_symbol_overlay(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    d = ImageDraw.Draw(img)
    fid = slide.get("feature_id", "")
    x, y, w, h = box
    if fid == "voice-navigation":
        _draw_voice_waves(d, box, accent)
        return
    if fid == "route-planner":
        _draw_itinerary_cards(img, box, accent)
        return
    if fid == "nearby-location":
        for px, py, label in [
            (x + int(w * .26), y + int(h * .48), "Fuel"),
            (x + int(w * .48), y + int(h * .34), "Food"),
            (x + int(w * .66), y + int(h * .58), "Share"),
        ]:
            _draw_map_pin(d, px, py, 58, accent, label)
        return
    if fid == "groups":
        _draw_record_stack(img, box, accent, "Groups")
        return
    if fid == "saved-measurements":
        _draw_record_stack(img, box, accent, "Saved")
        return
    if fid == "gps-gallery":
        _draw_record_stack(img, box, accent, "Photos")
        return
    if fid == "poi-markers":
        cx, cy = x + int(w * .39), y + int(h * .53)
        r = int(min(w, h) * .13)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*accent, 55), outline=(255, 255, 255), width=8)
        d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], outline=accent, width=6)
        pin_w, pin_h = int(r * .70), int(r * 1.12)
        d.ellipse([cx - pin_w // 2, cy - pin_h, cx + pin_w // 2, cy - pin_h + pin_w], fill=accent)
        d.polygon([(cx - pin_w // 3, cy - pin_h // 2), (cx + pin_w // 3, cy - pin_h // 2), (cx, cy + pin_h // 3)], fill=accent)
        d.ellipse([cx - pin_w // 7, cy - pin_h + pin_w // 3, cx + pin_w // 7, cy - pin_h + pin_w * 2 // 3], fill=(255, 255, 255))
        d.rounded_rectangle([cx + r // 2, cy - r, cx + r * 2 + 44, cy - r + 58], radius=20, fill=(255, 255, 255, 235))
        d.text((cx + r // 2 + 18, cy - r + 14), "Saved spot", font=_font(23, True), fill=(12, 25, 44))
        return
    if fid in {"area-measurement", "distance-tracking", "route-planner", "find-routes", "nearby-location", "groups", "saved-measurements"}:
        _draw_measure_overlay(d, box, accent, fid)
        return
    if fid == "speedometer":
        cx, cy, r = x + int(w * .31), y + int(h * .54), int(min(w, h) * .20)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(12, 18, 28, 220), outline=(255, 255, 255), width=6)
        for i in range(13):
            a = math.radians(205 + i * 11)
            x1, y1 = cx + int(math.cos(a) * r * .72), cy + int(math.sin(a) * r * .72)
            x2, y2 = cx + int(math.cos(a) * r * .90), cy + int(math.sin(a) * r * .90)
            d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255), width=3)
        d.text((cx - 44, cy - 26), "72", font=_font(50, True), fill=(255, 255, 255))
        d.text((cx - 36, cy + 28), "km/h", font=_font(22, True), fill=accent)
        return
    if fid == "compass":
        cx, cy, r = x + int(w * .32), y + int(h * .54), int(min(w, h) * .21)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 225), outline=accent, width=8)
        d.polygon([(cx, cy - r + 24), (cx + 24, cy), (cx, cy + r - 24), (cx - 24, cy)], fill=accent)
        d.text((cx - 17, cy - r + 34), "N", font=_font(28, True), fill=(12, 25, 44))
        return
    if fid in {"gps-camera", "gps-gallery"}:
        card_x, card_y = x + int(w * .08), y + int(h * .58)
        card_w, card_h = int(w * .44), int(h * .20)
        _shadow(img, (card_x + 8, card_y + 12, card_x + card_w + 8, card_y + card_h + 12), 26, 45, 14)
        d.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h], radius=26, fill=(12, 18, 28, 230))
        d.text((card_x + 28, card_y + 26), "GPS Stamp", font=_font(32, True), fill=(255, 255, 255))
        d.text((card_x + 28, card_y + 70), "Location | Date | Time", font=_font(22, True), fill=_mix(accent, (255, 255, 255), .25))
        d.rounded_rectangle([card_x + card_w - 100, card_y + 30, card_x + card_w - 34, card_y + 96], radius=20, fill=accent)
        d.ellipse([card_x + card_w - 82, card_y + 48, card_x + card_w - 52, card_y + 78], outline=(255, 255, 255), width=5)
        return
    if fid == "wonder-places":
        px, py = x + int(w * .13), y + int(h * .48)
        d.rounded_rectangle([px, py, px + int(w * .40), py + int(h * .19)], radius=26, fill=(255, 255, 255, 230))
        d.text((px + 28, py + 24), "Discover", font=_font(34, True), fill=(12, 25, 44))
        d.text((px + 28, py + 76), "wonders & famous places", font=_font(20, True), fill=(88, 105, 123))
        d.ellipse([x + int(w * .60), y + int(h * .36), x + int(w * .84), y + int(h * .60)], outline=accent, width=8)
        d.arc([x + int(w * .63), y + int(h * .39), x + int(w * .81), y + int(h * .57)], 90, 270, fill=accent, width=5)
        d.arc([x + int(w * .63), y + int(h * .39), x + int(w * .81), y + int(h * .57)], 270, 90, fill=accent, width=5)
        return
    if fid == "street-view":
        px, py = x + int(w * .13), y + int(h * .48)
        d.rounded_rectangle([px, py, px + int(w * .42), py + int(h * .19)], radius=26, fill=(255, 255, 255, 230))
        d.text((px + 28, py + 24), "360 Preview", font=_font(34, True), fill=(12, 25, 44))
        d.text((px + 28, py + 76), "street-level view", font=_font(22, True), fill=(88, 105, 123))
        cx, cy = x + int(w * .66), y + int(h * .48)
        d.ellipse([cx - 92, cy - 92, cx + 92, cy + 92], outline=accent, width=8)
        d.arc([cx - 130, cy - 54, cx + 130, cy + 54], 180, 360, fill=(255, 255, 255), width=7)
        d.polygon([(cx, cy - 72), (cx + 34, cy), (cx, cy + 72), (cx - 34, cy)], fill=accent)
        return
    _draw_measure_overlay(d, box, accent, fid)


def _draw_feature_scene(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int], seed: int) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    fid = slide.get("feature_id", "")
    scenario = slide.get("scenario", "")
    # Base layered environment
    d.rounded_rectangle([x, y, x + w, y + h], radius=36, fill=(207, 235, 249))
    photo_backdrop = False
    if fid in {
        "area-measurement", "distance-tracking", "poi-markers", "route-planner",
        "voice-navigation", "find-routes", "nearby-location", "saved-measurements",
        "groups", "gps-camera", "gps-gallery", "speedometer", "compass",
        "wonder-places", "street-view",
    }:
        photo_backdrop = _draw_context_backdrop(img, slide, box, accent, opacity=145)
    d.ellipse([x + int(w * .74), y + 28, x + int(w * .92), y + 28 + int(w * .18)], fill=(255, 210, 92))

    if fid == "voice-navigation":
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .50), x + w, y + h], fill=(108, 148, 174))
            d.polygon([(x, y + h), (x + int(w * .42), y + int(h * .48)), (x + int(w * .56), y + int(h * .48)), (x + int(w * .22), y + h)], fill=(64, 72, 82))
        _draw_voice_waves(d, box, accent)
        d.rounded_rectangle([x + int(w * .52), y + int(h * .34), x + int(w * .84), y + int(h * .48)], radius=28, fill=(255, 255, 255, 230))
        d.text((x + int(w * .55), y + int(h * .38)), "Speak destination", font=_font(24, True), fill=(12, 25, 44))
    elif fid == "route-planner":
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .48), x + w, y + h], fill=(164, 190, 152))
        _draw_itinerary_cards(img, box, accent)
        pts = [(x + int(w * .55), y + int(h * .72)), (x + int(w * .67), y + int(h * .52)), (x + int(w * .80), y + int(h * .63))]
        d.line(pts, fill=(255, 255, 255), width=11, joint="curve")
        d.line(pts, fill=accent, width=6, joint="curve")
        for px, py in pts:
            _draw_map_pin(d, px, py, 38, accent)
    elif fid in {"find-routes", "nearby-location"}:
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .48), x + w, y + h], fill=(164, 190, 152))
        d.polygon([(x, y + h), (x + int(w * .38), y + int(h * .42)), (x + int(w * .56), y + int(h * .42)), (x + int(w * .24), y + h)], fill=(72, 82, 91))
        d.line([(x + int(w * .40), y + int(h * .47)), (x + int(w * .27), y + h)], fill=(255, 255, 255), width=5)
        d.line([(x + int(w * .52), y + int(h * .47)), (x + int(w * .58), y + h)], fill=(255, 255, 255), width=5)
        for bx in range(x + 80, x + w, 170):
            d.rectangle([bx, y + int(h * .38), bx + 54, y + int(h * .52)], fill=(230, 235, 240))
            d.polygon([(bx, y + int(h * .38)), (bx + 28, y + int(h * .30)), (bx + 54, y + int(h * .38))], fill=(190, 202, 214))
        if fid == "nearby-location":
            for px, py, label in [
                (x + int(w * .26), y + int(h * .50), "Fuel"),
                (x + int(w * .50), y + int(h * .36), "Food"),
                (x + int(w * .72), y + int(h * .58), "Share"),
            ]:
                _draw_map_pin(d, px, py, 48, accent, label)
        else:
            _draw_measure_overlay(d, box, accent, fid)
    elif fid == "poi-markers":
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .48), x + w, y + h], fill=(168, 188, 160))
            for i in range(4):
                y0 = y + int(h * (.55 + i * .08))
                d.polygon([(x, y0), (x + w, y0 - 55), (x + w, y0 + 48), (x, y0 + 92)], fill=_mix(accent, (255, 255, 255), .52 + i * .05))
        for px, py, label in [
            (x + int(w * .30), y + int(h * .42), "Gate"),
            (x + int(w * .54), y + int(h * .58), "Pump"),
            (x + int(w * .73), y + int(h * .36), "Plot"),
        ]:
            d.ellipse([px - 24, py - 52, px + 24, py - 4], fill=accent, outline=(255, 255, 255), width=5)
            d.polygon([(px - 16, py - 18), (px + 16, py - 18), (px, py + 18)], fill=accent)
            d.ellipse([px - 7, py - 36, px + 7, py - 22], fill=(255, 255, 255))
            d.rounded_rectangle([px + 20, py - 52, px + 124, py - 14], radius=14, fill=(255, 255, 255, 235))
            d.text((px + 34, py - 45), label, font=_font(19, True), fill=(12, 25, 44))
    elif fid in {"speedometer"}:
        d.rectangle([x, y + int(h * .42), x + w, y + h], fill=(54, 63, 72))
        for off in range(-80, w, 190):
            d.polygon([(x + off, y + h), (x + off + 96, y + int(h * .42)), (x + off + 126, y + int(h * .42)), (x + off + 30, y + h)], fill=(245, 198, 75))
        gx, gy, gr = x + int(w * .35), y + int(h * .55), int(min(w, h) * .22)
        d.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=(18, 27, 41), outline=(255, 255, 255), width=6)
        for a in range(210, 511, 30):
            rad = math.radians(a)
            x1, y1 = gx + int(math.cos(rad) * gr * .78), gy + int(math.sin(rad) * gr * .78)
            x2, y2 = gx + int(math.cos(rad) * gr * .92), gy + int(math.sin(rad) * gr * .92)
            d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255), width=3)
        d.text((gx - 42, gy - 22), "72", font=_font(48, True), fill=(255, 255, 255))
        d.text((gx - 36, gy + 30), "km/h", font=_font(22, True), fill=accent)
    elif fid in {"compass"}:
        d.rectangle([x, y + int(h * .55), x + w, y + h], fill=(106, 159, 113))
        d.polygon([(x, y + int(h * .58)), (x + int(w * .25), y + int(h * .34)), (x + int(w * .48), y + int(h * .58))], fill=(112, 133, 142))
        d.polygon([(x + int(w * .33), y + int(h * .60)), (x + int(w * .62), y + int(h * .28)), (x + w, y + int(h * .60))], fill=(92, 113, 122))
        cx, cy, r = x + int(w * .42), y + int(h * .58), int(min(w, h) * .23)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=accent, width=10)
        d.polygon([(cx, cy - r + 28), (cx + 28, cy), (cx, cy + r - 28), (cx - 28, cy)], fill=accent)
        d.text((cx - 17, cy - r + 42), "N", font=_font(28, True), fill=(12, 25, 44))
    elif fid in {"groups", "saved-measurements"}:
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .52), x + w, y + h], fill=(96, 174, 92))
        _draw_record_stack(img, box, accent, "Groups" if fid == "groups" else "Saved")
    elif fid == "gps-gallery":
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .52), x + w, y + h], fill=(178, 195, 171))
        _draw_record_stack(img, box, accent, "Photos")
        for i in range(3):
            px = x + int(w * (.52 + i * .10))
            py = y + int(h * (.26 + i * .08))
            d.rounded_rectangle([px, py, px + 110, py + 140], radius=16, fill=(255, 255, 255), outline=_mix(accent, (255, 255, 255), .30), width=3)
            d.rectangle([px + 12, py + 12, px + 98, py + 84], fill=_mix(accent, (255, 255, 255), .55))
            d.text((px + 18, py + 100), "GPS", font=_font(16, True), fill=(12, 25, 44))
    elif fid == "gps-camera":
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .52), x + w, y + h], fill=(178, 195, 171))
        for i in range(3):
            px = x + int(w * (.14 + i * .2))
            py = y + int(h * (.22 + (i % 2) * .08))
            _shadow(img, (px + 10, py + 12, px + 180, py + 230), 18, 40, 12)
            d.rounded_rectangle([px, py, px + 170, py + 220], radius=18, fill=(255, 255, 255))
            d.rectangle([px + 14, py + 14, px + 156, py + 130], fill=_mix(accent, (255, 255, 255), .62))
            d.text((px + 20, py + 150), "GPS", font=_font(28, True), fill=(12, 25, 44))
            d.text((px + 20, py + 184), "Stamped", font=_font(18, True), fill=(93, 112, 131))
        d.rounded_rectangle([x + int(w * .70), y + int(h * .24), x + int(w * .88), y + int(h * .50)], radius=28, fill=(18, 27, 41))
        d.ellipse([x + int(w * .75), y + int(h * .31), x + int(w * .83), y + int(h * .43)], fill=accent)
    elif fid == "wonder-places":
        d.rectangle([x, y + int(h * .54), x + w, y + h], fill=(188, 170, 138))
        for i, (bw, bh) in enumerate([(74, 132), (96, 174), (68, 116), (110, 152)]):
            bx = x + 58 + i * 150
            base = y + int(h * .58)
            d.rectangle([bx, base - bh, bx + bw, base], fill=(226, 232, 238))
            d.polygon([(bx - 12, base - bh), (bx + bw // 2, base - bh - 48), (bx + bw + 12, base - bh)], fill=_mix(accent, (255, 255, 255), .48))
        _draw_feature_symbol_overlay(img, slide, box, accent)
    elif fid == "street-view":
        d.rectangle([x, y + int(h * .54), x + w, y + h], fill=(188, 170, 138))
        for i in range(7):
            bx = x + 40 + i * 115
            bh = 110 + (i % 3) * 38
            d.rectangle([bx, y + int(h * .55) - bh, bx + 72, y + int(h * .55)], fill=(226, 232, 238))
            d.rectangle([bx + 16, y + int(h * .55) - bh + 22, bx + 56, y + int(h * .55) - bh + 48], fill=(143, 182, 210))
        d.rounded_rectangle([x + int(w * .20), y + int(h * .62), x + int(w * .84), y + int(h * .82)], radius=34, fill=(75, 85, 93))
        d.line([(x + int(w * .24), y + int(h * .72)), (x + int(w * .80), y + int(h * .72))], fill=(255, 255, 255), width=5)
        _draw_feature_symbol_overlay(img, slide, box, accent)
    else:
        if not photo_backdrop:
            d.rectangle([x, y + int(h * .52), x + w, y + h], fill=(96, 174, 92))
            for i, color in enumerate([(135, 194, 100), (218, 184, 104), (111, 179, 86), (164, 205, 117)]):
                y0 = y + int(h * (.58 + i * .09))
                d.polygon([(x, y0), (x + w, y0 - 70), (x + w, y0 + 72), (x, y0 + 118)], fill=color)
        _draw_measure_overlay(d, box, accent, fid)

    if fid in {"area-measurement", "distance-tracking", "poi-markers", "groups", "saved-measurements", "gps-camera", "gps-gallery"}:
        _draw_person(img, x + int(w * .04), y + int(h * .37), max(.72, w / 980), accent, hard_hat=fid != "area-measurement")

    # Floating 3D feature tiles make screenshot-led posts feel designed, not dumped.
    for i in range(3):
        tx = x + int(w * (.18 + i * .21))
        ty = y + int(h * (.18 + (i % 2) * .08))
        tile_color = _mix(accent, (255, 255, 255), .20 + i * .12)
        _draw_isometric_tile(d, tx, ty, int(w * .14), int(h * .08), tile_color)


def _draw_text_panel(
    img: Image.Image,
    slide: dict,
    box: tuple[int, int, int, int],
    accent: tuple[int, int, int],
    *,
    compact: bool = False,
    dark: bool = False,
) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    radius = 38
    _shadow(img, (x + 12, y + 18, x + w + 12, y + h + 18), radius, 48, 20)
    fill = (12, 18, 28, 235) if dark else (255, 255, 255, 255)
    title_color = (255, 255, 255) if dark else (10, 22, 40)
    body_color = (204, 214, 224) if dark else (88, 105, 123)
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill)

    brand_size = max(48, int(w * (.065 if compact else .08)))
    _draw_app_icon(img, slide, x + 34, y + 34, brand_size)
    d.text((x + 48 + brand_size, y + 40), slide.get("app_name", "FieldCalc"), font=_font(max(24, int(w * .04)), True), fill=title_color)
    if slide.get("eyebrow"):
        label = str(slide["eyebrow"])
        f = _font(max(17, int(w * .026)), True)
        tw = int(d.textlength(label, font=f))
        d.rounded_rectangle([x + w - tw - 72, y + 42, x + w - 34, y + 82], radius=20, fill=_mix(accent, (255, 255, 255), .12))
        d.text((x + w - tw - 54, y + 51), label, font=f, fill=(255, 255, 255))

    cta_y = y + h - 74
    chip_y = cta_y - 56
    title_font = _font(max(30, int(w * (.045 if compact else .072))), True)
    title_y = y + (102 if compact else 118)
    for line in _wrap_lines(slide.get("title", ""), title_font, w - 76, 2):
        d.text((x + 38, title_y), line, font=title_font, fill=title_color)
        title_y += int(title_font.size * 1.04)

    sub_font = _font(max(21, int(w * .034)))
    sub_y = title_y + 18
    subtitle = slide.get("subtitle") or slide.get("caption_text") or ""
    available_sub_lines = max(0, min(2, (chip_y - sub_y - 10) // max(1, int(sub_font.size * 1.28))))
    for line in _wrap_lines(subtitle, sub_font, w - 84, available_sub_lines):
        d.text((x + 38, sub_y), line, font=sub_font, fill=body_color)
        sub_y += int(sub_font.size * 1.32)

    chip_font = _font(max(18, int(w * .03)), True)
    cx = x + 38
    panel_chips = [] if compact and h < 285 else (slide.get("chips") or [])[:3]
    for chip in panel_chips:
        label = str(chip)
        cw = int(d.textlength(label, font=chip_font)) + 32
        if cx + cw > x + w - 36:
            break
        chip_fill = (28, 38, 54) if dark else (235, 246, 241)
        chip_outline = (58, 73, 94) if dark else (207, 226, 218)
        d.rounded_rectangle([cx, chip_y, cx + cw, chip_y + 42], radius=20, fill=chip_fill, outline=chip_outline)
        d.text((cx + 16, chip_y + 10), label, font=chip_font, fill=title_color)
        cx += cw + 10

    cta_w = min(w - 76, max(300, int(w * .54)))
    d.rounded_rectangle([x + 38, cta_y, x + 38 + cta_w, cta_y + 54], radius=22, fill=accent)
    d.text((x + 64, cta_y + 13), slide.get("footer", "Install Free"), font=_font(max(21, int(w * .035)), True), fill=(255, 255, 255))


def _draw_feature_badge(d: ImageDraw.ImageDraw, xy: tuple[int, int], label: str, accent: tuple[int, int, int]) -> None:
    x, y = xy
    font = _font(26, True)
    tw = int(d.textlength(label, font=font))
    d.rounded_rectangle([x, y, x + tw + 42, y + 48], radius=20, fill=(*accent, 235))
    d.text((x + 21, y + 11), label, font=font, fill=(255, 255, 255))


def _draw_step_cards(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    items = slide.get("bullets") or slide.get("chips") or ["Open feature", "Add points", "Save result"]
    items = list(items)[:3]
    card_w = (w - 44) // 3
    for i, item in enumerate(items):
        cx = x + i * (card_w + 22)
        _shadow(img, (cx + 8, y + 10, cx + card_w + 8, y + h + 10), 26, 40, 14)
        d.rounded_rectangle([cx, y, cx + card_w, y + h], radius=26, fill=(255, 255, 255, 250))
        d.ellipse([cx + 24, y + 20, cx + 76, y + 72], fill=(*accent, 230))
        d.text((cx + 42, y + 32), str(i + 1), font=_font(26, True), fill=(255, 255, 255))
        label_font = _font(max(18, min(24, h // 6)), True)
        text_y = y + 92
        for line in _wrap_lines(str(item).title(), label_font, card_w - 48, 2):
            d.text((cx + 24, text_y), line, font=label_font, fill=(12, 25, 44))
            text_y += int(label_font.size * 1.15)


def _draw_comparison_cards(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    gap = 26
    card_w = (w - gap) // 2
    labels = [("Before", "Guessing, measuring tape, scattered notes"), ("With FieldCalc", slide.get("subtitle") or "Measure and save it in the app")]
    for i, (head, body) in enumerate(labels):
        cx = x + i * (card_w + gap)
        fill = (255, 255, 255, 245) if i else (247, 241, 237, 245)
        d.rounded_rectangle([cx, y, cx + card_w, y + h], radius=30, fill=fill)
        d.text((cx + 34, y + 30), head, font=_font(32, True), fill=accent if i else (132, 84, 74))
        line_y = y + 92
        for line in _wrap_lines(body, _font(25, True), card_w - 68, 3):
            d.text((cx + 34, line_y), line, font=_font(25, True), fill=(34, 46, 62))
            line_y += 34
        if i:
            d.ellipse([cx + card_w - 92, y + h - 88, cx + card_w - 34, y + h - 30], fill=(*accent, 230))
            d.line([(cx + card_w - 76, y + h - 58), (cx + card_w - 62, y + h - 44), (cx + card_w - 44, y + h - 72)], fill=(255, 255, 255), width=6)
        else:
            for j in range(5):
                px = cx + 56 + j * 54
                py = y + h - 92 + (j % 2) * 18
                d.line([(px, py), (px + 34, py - 56)], fill=(132, 84, 74), width=5)


def _draw_slim_footer(img: Image.Image, slide: dict, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    _shadow(img, (x + 8, y + 12, x + w + 8, y + h + 12), 34, 40, 16)
    d.rounded_rectangle([x, y, x + w, y + h], radius=34, fill=(255, 255, 255, 252))
    compact = h < 190
    icon_size = max(44, int(h * (.36 if compact else .42)))
    icon_y = y + max(18, int(h * .20))
    _draw_app_icon(img, slide, x + 30, icon_y, icon_size)
    text_x = x + 48 + icon_size
    d.text((text_x, y + (22 if compact else 30)), slide.get("app_name", "FieldCalc"), font=_font(24 if compact else 30, True), fill=(12, 25, 44))
    title_font = _font(28 if compact else 40, True)
    title_y = y + (58 if compact else 72)
    for line in _wrap_lines(slide.get("title", ""), title_font, int(w * (.46 if compact else .56)), 1):
        d.text((text_x, title_y), line, font=title_font, fill=(10, 22, 40))
    label = slide.get("eyebrow") or "Comparison"
    badge_font = _font(24, True)
    tw = int(d.textlength(label, font=badge_font))
    d.rounded_rectangle([x + w - tw - 64, y + 34, x + w - 32, y + 76], radius=20, fill=accent)
    d.text((x + w - tw - 48, y + 43), label, font=badge_font, fill=(255, 255, 255))
    cta = slide.get("footer", "Download")
    cta_font = _font(23 if compact else 28, True)
    cta_w = min(int(w * (.44 if compact else .62)), int(d.textlength(cta, font=cta_font)) + 46)
    cta_x = x + (w - cta_w - 32 if compact else 38)
    cta_y = y + h - (56 if compact else 64)
    if not compact:
        d.rounded_rectangle([cta_x, cta_y, cta_x + cta_w, cta_y + 46], radius=20, fill=accent)
        d.text((cta_x + 27, cta_y + 9), cta, font=cta_font, fill=(255, 255, 255))


def _seed_variant(seed_key: str, seed: int, modulo: int) -> int:
    digits = "".join(ch for ch in str(seed_key or "") if ch.isdigit())
    if digits:
        return int(digits[-8:]) % modulo
    return seed % modulo


def _draw_ui_showcase_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    w, h = img.size
    d = ImageDraw.Draw(img)
    _draw_gradient(img, _mix(accent, (255, 255, 255), .76), (247, 250, 252))
    _draw_feature_scene(img, slide, (42, 48, w - 84, int(h * .56)), accent, seed)
    phone_w = int(w * (0.42 if h <= w else 0.46))
    phone_h = int(phone_w * 1.78)
    phone_x = w - phone_w - 88
    phone_y = 112 if h <= w else 164
    _draw_phone(img, slide, (phone_x, phone_y, phone_w, min(phone_h, int(h * .70))), accent)
    panel_w = int(w * .48)
    panel_h = int(h * .42)
    _draw_text_panel(img, slide, (58, h - panel_h - 74, panel_w, panel_h), accent, compact=True)
    _draw_feature_badge(d, (58, 58), slide.get("creative_image_label") or slide.get("eyebrow") or "Feature", accent)


def _draw_problem_solution_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    w, h = img.size
    _draw_gradient(img, (252, 247, 244), _mix(accent, (255, 255, 255), .86))
    scene_h = int(h * .42)
    _draw_feature_scene(img, slide, (42, 48, w - 84, scene_h), accent, seed)
    _draw_comparison_cards(img, slide, (70, int(h * .52), w - 140, int(h * .18)), accent)
    _draw_slim_footer(img, slide, (70, h - int(h * .19) - 54, w - 140, int(h * .17)), accent)


def _draw_minimal_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    w, h = img.size
    d = ImageDraw.Draw(img)
    _draw_gradient(img, (11, 18, 31), _mix(accent, (8, 12, 20), .70))
    scene_box = (70, 116, w - 140, int(h * .46))
    _draw_feature_scene(img, slide, scene_box, accent, seed)
    d.rounded_rectangle([70, 70, w - 70, h - 70], radius=48, outline=(*accent, 130), width=3)
    _draw_text_panel(img, slide, (90, h - int(h * .34) - 92, w - 180, int(h * .30)), accent, compact=True, dark=True)


def _draw_tutorial_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    w, h = img.size
    _draw_gradient(img, _mix(accent, (255, 255, 255), .83), (248, 251, 249))
    scene_box = (42, 48, w - 84, int(h * .48))
    _draw_feature_scene(img, slide, scene_box, accent, seed)
    if slide.get("screenshot_path") or slide.get("screenshot_url"):
        _draw_phone(img, slide, (int(w * .63), int(h * .10), int(w * .27), int(h * .40)), accent)
    _draw_step_cards(img, slide, (70, int(h * .52), w - 140, int(h * .15)), accent)
    _draw_text_panel(img, slide, (70, int(h * .70), w - 140, int(h * .24)), accent, compact=True)


def _draw_cinematic_scene_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    """Full-bleed feature scene with a sharp marketing title band."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    scene_asset = _slide_asset_path(slide, "scene_asset_path")
    if scene_asset:
        _draw_photo_backdrop(img, scene_asset, (0, 0, w, h), accent, y_bias=(seed % 10) / 10, blur=0.15, dim=34, tint=18)
    else:
        _draw_gradient(img, _mix(accent, (255, 255, 255), .72), (238, 244, 248))
        _draw_feature_scene(img, slide, (36, 42, w - 72, int(h * .62)), accent, seed)

    veil = Image.new("RGBA", img.size, (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    vd.rectangle([0, int(h * .46), w, h], fill=(6, 12, 20, 132))
    vd.rectangle([0, 0, w, int(h * .18)], fill=(255, 255, 255, 82))
    img.alpha_composite(veil)

    feature_box = (44, int(h * .12), w - 88, int(h * .45))
    _draw_feature_symbol_overlay(img, slide, feature_box, accent)

    if slide.get("screenshot_path") or slide.get("screenshot_url"):
        phone_w = int(w * (.31 if h <= w else .35))
        phone_h = int(phone_w * 1.78)
        _draw_phone(img, slide, (w - phone_w - 62, int(h * .22), phone_w, min(phone_h, int(h * .56))), accent)

    _draw_app_icon(img, slide, 58, 58, 76)
    d.text((154, 68), slide.get("app_name", "FieldCalc"), font=_font(34, True), fill=(10, 22, 40))
    d.text((58, int(h * .61)), slide.get("eyebrow") or slide.get("creative_image_label") or "Feature", font=_font(30, True), fill=_mix(accent, (255, 255, 255), .08))
    title_font = _font(76 if h > w else 64, True)
    ty = int(h * .66)
    for line in _wrap_lines(slide.get("title", ""), title_font, int(w * .70), 2):
        d.text((58, ty), line, font=title_font, fill=(255, 255, 255))
        ty += int(title_font.size * 1.04)
    sub_font = _font(31 if h > w else 27)
    for line in _wrap_lines(slide.get("subtitle", ""), sub_font, int(w * .78), 2):
        d.text((58, ty + 18), line, font=sub_font, fill=(218, 228, 238))
        ty += int(sub_font.size * 1.22)
    cta = slide.get("footer", "Install Free")
    cta_font = _font(30, True)
    cta_w = int(d.textlength(cta, font=cta_font)) + 62
    d.rounded_rectangle([58, h - 104, 58 + cta_w, h - 46], radius=24, fill=accent)
    d.text((89, h - 91), cta, font=cta_font, fill=(255, 255, 255))


def _draw_layered_mockup_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    """Premium app-led layout with floating 3D cards and a real Android screen."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    _draw_gradient(img, (245, 249, 252), _mix(accent, (255, 255, 255), .78))
    for i in range(9):
        cx = 86 + (i % 3) * int(w * .16)
        cy = 96 + (i // 3) * int(h * .105)
        _draw_isometric_tile(d, cx, cy, int(w * .12), int(h * .055), _mix(accent, (255, 255, 255), .18 + (i % 3) * .12))

    scene_box = (44, 48, w - 88, int(h * .48))
    _draw_feature_scene(img, slide, scene_box, accent, seed)
    scrim = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    sd.rounded_rectangle([scene_box[0], scene_box[1], scene_box[0] + scene_box[2], scene_box[1] + scene_box[3]], radius=36, fill=(255, 255, 255, 68))
    img.alpha_composite(scrim)

    if slide.get("screenshot_path") or slide.get("screenshot_url"):
        phone_w = int(w * (.39 if h > w else .34))
        _draw_phone(img, slide, (w - phone_w - 76, int(h * .11), phone_w, int(phone_w * 1.78)), accent)
    _draw_text_panel(img, slide, (58, int(h * .58), w - 116, int(h * .32)), accent, compact=True)


def _draw_editorial_grid_layout(img: Image.Image, slide: dict, accent: tuple[int, int, int], seed: int) -> None:
    """Magazine-style grid so FAQ/tip/use-case posts stop looking like one template."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    _draw_gradient(img, (250, 251, 248), _mix(accent, (255, 255, 255), .86))
    margin = 54
    top_h = int(h * .50)
    left_w = int(w * .52)
    _draw_feature_scene(img, slide, (margin, margin, left_w, top_h), accent, seed)
    if slide.get("screenshot_path") or slide.get("screenshot_url"):
        _draw_phone(img, slide, (margin + left_w + 34, margin + 12, int(w * .30), int(h * .43)), accent)
    else:
        _draw_feature_symbol_overlay(img, slide, (margin + left_w + 20, margin, w - margin - left_w - 40, top_h), accent)

    badge = slide.get("creative_image_label") or slide.get("eyebrow") or "Feature"
    _draw_feature_badge(d, (margin, margin + top_h + 34), badge, accent)
    title_font = _font(68 if h > w else 56, True)
    ty = margin + top_h + 104
    for line in _wrap_lines(slide.get("title", ""), title_font, w - margin * 2, 2):
        d.text((margin, ty), line, font=title_font, fill=(10, 22, 40))
        ty += int(title_font.size * 1.05)
    sub_font = _font(30 if h > w else 26)
    for line in _wrap_lines(slide.get("subtitle", ""), sub_font, w - margin * 2, 3):
        d.text((margin, ty + 16), line, font=sub_font, fill=(82, 99, 118))
        ty += int(sub_font.size * 1.25)
    _draw_slim_footer(img, slide, (margin, h - 170, w - margin * 2, 116), accent)


def _render_pillow(slide: dict, out_path: str) -> str:
    w, h = slide["w"], slide["h"]
    accent = _rgb(slide.get("accent", "#0aa77f"))
    seed_basis = "|".join([
        str(slide.get("render_seed_key") or ""),
        str(slide.get("title", "") or ""),
        str(slide.get("angle_id", "") or ""),
        str(slide.get("feature_id", "") or ""),
        str(slide.get("index", "") or ""),
    ])
    seed = int(hashlib.sha256(seed_basis.encode("utf-8")).hexdigest()[:8], 16)

    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    image_type = slide.get("creative_image_type") or ""
    angle_id = slide.get("angle_id") or ""
    layout_family = image_type
    if not layout_family:
        layout_family = {
            "tutorial": "tutorial_step",
            "comparison": "problem_solution",
            "tip-trick": "minimal_brand_post",
            "faq": "clean_feature_explainer",
            "bts": "clean_feature_explainer",
        }.get(angle_id, "realistic_scenario")
    if slide.get("forced_visual_variant") is not None:
        try:
            creative_variant = int(slide["forced_visual_variant"]) % 5
        except (TypeError, ValueError):
            creative_variant = _seed_variant(slide.get("render_seed_key", ""), seed, 5)
    else:
        creative_variant = _seed_variant(slide.get("render_seed_key", ""), seed, 5)
    if slide.get("kind") in {"point", "cta"} and layout_family == "tutorial_step":
        layout_family = ["app_ui_showcase", "clean_feature_explainer", "problem_solution"][int(slide.get("index", 0)) % 3]

    if slide.get("kind") in {"single", "hook"}:
        if layout_family in {"realistic_scenario", "realistic_scenario_with_android_mockup"}:
            layout_family = [
                "cinematic_scene",
                "layered_mockup",
                "photo_hero",
                "editorial_grid",
                "app_ui_showcase",
            ][creative_variant]
        elif layout_family == "clean_feature_explainer":
            layout_family = ["editorial_grid", "layered_mockup", "photo_hero", "minimal_brand_post", "cinematic_scene"][creative_variant]

    if layout_family in {"app_ui_showcase", "realistic_scenario_with_android_mockup"}:
        _draw_ui_showcase_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path
    if layout_family == "cinematic_scene":
        _draw_cinematic_scene_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path
    if layout_family == "layered_mockup":
        _draw_layered_mockup_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path
    if layout_family == "editorial_grid":
        _draw_editorial_grid_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path
    if layout_family == "problem_solution":
        _draw_problem_solution_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path
    if layout_family == "minimal_brand_post":
        _draw_minimal_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path
    if layout_family == "tutorial_step":
        tutorial_variant = creative_variant
        if tutorial_variant == 1:
            _draw_ui_showcase_layout(img, slide, accent, seed)
            img.convert("RGB").save(out_path)
            return out_path
        if tutorial_variant == 2:
            _draw_cinematic_scene_layout(img, slide, accent, seed)
            img.convert("RGB").save(out_path)
            return out_path
        if tutorial_variant == 3:
            _draw_layered_mockup_layout(img, slide, accent, seed)
            img.convert("RGB").save(out_path)
            return out_path
        if tutorial_variant == 4:
            _draw_editorial_grid_layout(img, slide, accent, seed)
            img.convert("RGB").save(out_path)
            return out_path
        _draw_tutorial_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path

    if layout_family in {"photo_hero", "realistic_scenario", "clean_feature_explainer"} and slide.get("scene_asset_path"):
        _draw_photo_hero_layout(img, slide, accent, seed)
        img.convert("RGB").save(out_path)
        return out_path

    _draw_gradient(img, _mix(accent, (255, 255, 255), .82), (246, 250, 247))
    d = ImageDraw.Draw(img)
    for i in range(18):
        ox = (seed // (i + 3)) % w
        oy = (seed // (i + 7)) % h
        r = 4 + (i % 4) * 4
        d.ellipse([ox, oy, ox + r, oy + r], fill=(*_mix(accent, (255, 255, 255), .65), 80))

    if h > w * 1.45:
        scene_box = (44, 80, w - 88, int(h * .54))
        panel_box = (54, int(h * .58), w - 108, int(h * .34))
        phone_box = (int(w * .58), int(h * .17), int(w * .30), int(h * .37))
    elif h > w:
        scene_box = (44, 54, w - 88, int(h * .58))
        panel_box = (58, int(h * .62), w - 116, int(h * .31))
        phone_box = (int(w * .61), int(h * .12), int(w * .29), int(h * .43))
    else:
        scene_box = (42, 48, w - 84, int(h * .56))
        panel_box = (58, int(h * .62), w - 116, int(h * .31))
        phone_box = (int(w * .61), int(h * .11), int(w * .29), int(h * .43))

    _shadow(img, (scene_box[0] + 10, scene_box[1] + 16, scene_box[0] + scene_box[2] + 10, scene_box[1] + scene_box[3] + 16), 42, 44, 18)
    _draw_feature_scene(img, slide, scene_box, accent, seed)
    if (slide.get("screenshot_path") or slide.get("screenshot_url")) and slide.get("creative_image_type") != "minimal_brand_post":
        _draw_phone(img, slide, phone_box, accent)
    _draw_text_panel(img, slide, panel_box, accent)

    if slide.get("index") is not None and slide.get("total"):
        dot_y = h - 28
        total = int(slide["total"])
        start = w // 2 - total * 12
        for i in range(total):
            fill = accent if i == int(slide.get("index", 0)) else (190, 202, 214)
            d.ellipse([start + i * 24, dot_y, start + i * 24 + 10, dot_y + 10], fill=fill)

    img.convert("RGB").save(out_path)
    return out_path


# --------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------
def _paste_premium_app_icon(img: Image.Image, icon_path: str | None) -> Image.Image:
    """Repair the branded tile on AI premium assets with the real launcher icon."""
    if not icon_path:
        return img
    try:
        icon = Image.open(icon_path).convert("RGBA")
    except Exception:
        return img

    canvas = img.convert("RGBA")
    w, h = canvas.size
    if h < int(w * 1.35):
        return canvas.convert("RGB")

    tile = int(w * 0.215)
    x = int(w * 0.045)
    y = int(h * 0.688)
    radius = int(tile * 0.18)
    clear_x = x - int(w * 0.008)
    clear_y = y - int(h * 0.006)
    clear_size = tile + int(w * 0.016)
    clear_radius = int(clear_size * 0.2)

    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(
        [clear_x, clear_y, clear_x + clear_size, clear_y + clear_size],
        radius=clear_radius,
        fill=(255, 255, 255, 255),
    )
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x + 8, y + 10, x + tile + 8, y + tile + 10], radius=radius, fill=(0, 0, 0, 42))
    canvas.alpha_composite(shadow)

    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x, y, x + tile, y + tile], radius=radius, fill=(255, 255, 255, 255))

    icon = icon.resize((tile, tile))
    mask = Image.new("L", (tile, tile), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, tile, tile], radius=radius, fill=255)
    ix = x + (tile - icon.width) // 2
    iy = y + (tile - icon.height) // 2
    canvas.paste(icon, (ix, iy), mask)

    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x, y, x + tile, y + tile], radius=radius, outline=(226, 232, 240, 255), width=2)
    return canvas.convert("RGB")


def _premium_variant(img: Image.Image, post_key: str, slide: dict) -> Image.Image:
    """Create a non-identical variant from a curated realistic asset."""
    seed = int(hashlib.sha256(post_key.encode("utf-8")).hexdigest()[:8], 16)
    variant = seed % 6
    w, h = img.size

    zoom = 1.0 + (variant % 3) * 0.018
    if zoom > 1:
        zw, zh = int(w / zoom), int(h / zoom)
        max_dx, max_dy = w - zw, h - zh
        x = int((seed % 100) / 100 * max_dx)
        y = int(((seed // 100) % 100) / 100 * max_dy)
        img = img.crop((x, y, x + zw, y + zh)).resize((w, h), Image.Resampling.LANCZOS)

    if variant in {1, 4}:
        img = ImageEnhance.Color(img).enhance(1.08)
        img = ImageEnhance.Contrast(img).enhance(1.04)
    elif variant in {2, 5}:
        img = ImageEnhance.Brightness(img).enhance(1.03)
        img = ImageEnhance.Contrast(img).enhance(1.08)
    else:
        img = ImageEnhance.Sharpness(img).enhance(1.08)

    canvas = img.convert("RGBA")
    d = ImageDraw.Draw(canvas)
    accent_hex = str(slide.get("accent", "#0aa77f")).lstrip("#")
    try:
        accent = tuple(int(accent_hex[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        accent = (10, 167, 127)

    if variant in {1, 3, 5}:
        border = max(8, int(w * 0.012))
        d.rounded_rectangle(
            [border // 2, border // 2, w - border // 2, h - border // 2],
            radius=int(w * 0.035),
            outline=(*accent, 210),
            width=border,
        )

    if variant in {2, 4, 5}:
        label = slide.get("eyebrow") or slide.get("creative_image_label") or "Feature"
        font = _font(max(26, int(w * 0.034)), True)
        tw = int(d.textlength(label, font=font))
        bx = int(w * 0.055)
        by = int(h * 0.055)
        d.rounded_rectangle([bx, by, bx + tw + 42, by + 58], radius=20, fill=(*accent, 225))
        d.text((bx + 21, by + 13), label, font=font, fill=(255, 255, 255, 255))

    return canvas.convert("RGB")


def generate_assets(
    feature: dict, angle: dict, post_format: PostFormat, post_key: str,
    content: Optional[dict] = None, strategy: Optional[dict] = None,
    use_realistic_asset: bool = True,
) -> List[str]:
    # New default path: generate exactly ONE photorealistic AI image with a clean
    # text overlay, regardless of the requested post_format. Carousel is never used.
    single_slides = _build_slides(feature, angle, PostFormat.SINGLE, content, strategy=strategy)
    if single_slides:
        ai_slide = single_slides[0]
        ai_slide["render_seed_key"] = f"{post_key}:0:SINGLE"
        try:
            from app import image_gen
            ai_path = image_gen.generate_realistic_post(
                feature, angle, post_key, ai_slide, content or {}
            )
        except Exception as e:  # noqa: BLE001 - AI path must never break generation
            log.warning("AI image module error for %s: %s", post_key, e)
            ai_path = None
        if ai_path:
            log.info("AI image generated for %s", post_key)
            return [ai_path]
        log.warning("AI generation failed, using pillow fallback for %s", post_key)

    # Fallback: ALWAYS generate a single image — never carousel.
    slides = _build_slides(feature, angle, PostFormat.SINGLE, content, strategy=strategy)
    for i, slide in enumerate(slides):
        slide["render_seed_key"] = f"{post_key}:{i}:SINGLE"
    use_full_bleed_realistic_asset = (
        use_realistic_asset
        and slides
        and slides[0].get("realistic_asset_path")
        and strategy
        and strategy.get("image_type_id") == "trust_growth_post"
    )
    if use_full_bleed_realistic_asset:
        src = Path(slides[0]["realistic_asset_path"])
        out = OUT_DIR / f"{post_key}_premium.png"
        img = Image.open(src).convert("RGB")
        img = _paste_premium_app_icon(img, _existing_asset_path(_load_visual_assets().get("defaults", {}).get("app_icon")))
        img = _premium_variant(img, post_key, slides[0])
        img.save(out)
        log.info("used premium realistic asset for %s [SINGLE]", post_key)
        return [str(out)]

    # The browser/HTML renderer is useful for simple template proofs, but the
    # dashboard needs the richer screenshot + scenario composition for live
    # marketing posts. Keep the enhanced Pillow renderer as the default path for
    # non-premium assets so regenerate/create do not fall back to dark text-only
    # posters.
    use_html = False
    paths: List[str] = []
    for i, slide in enumerate(slides):
        suffix = f"_slide{i}" if len(slides) > 1 else ""
        out = str(OUT_DIR / f"{post_key}{suffix}.png")
        try:
            if use_html:
                render_html.render_html(slide, out)
            else:
                _render_pillow(slide, out)
        except Exception as e:  # noqa: BLE001 — last-resort fallback
            log.warning("HTML render failed (%s); Pillow fallback for slide %d", e, i)
            _render_pillow(slide, out)
        paths.append(out)
    log.info("generated %d asset(s) for %s [%s] engine=%s",
             len(paths), post_key, post_format.value, "html" if use_html else "pillow")
    return paths
