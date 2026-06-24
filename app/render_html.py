"""HTML -> PNG renderer via headless Chromium (Playwright). This is the PRIMARY
image engine — real CSS gives us gradients, web typography, chips, and layered
design that Pillow can't match.

is_available() lets the caller fall back to Pillow when Chromium isn't installed
(e.g. CI without `playwright install chromium`), so the pipeline never hard-fails.
"""
from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger("autopost")

TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape())

# Scale-dependent typography per canvas height so text fits every format.
_DEVICE_SCALE = 2  # crisp 2x output


def _typography(w: int, h: int) -> dict:
    base = h / 1080.0  # 1.0 for square, 1.25 for 4:5, 1.78 for story
    s = lambda px: int(px * (0.85 + 0.15 * base))  # noqa: E731
    return {
        "w": w, "h": h, "pad": int(80 * (w / 1080.0)),
        "fs": {
            "brand": s(40), "eyebrow": s(22), "icon": s(150),
            "title": s(86), "sub": s(40), "bullet": s(40),
            "chip": s(28), "cta": s(40), "swipe": s(30),
        },
        "gap": {"icon": s(20), "sub": s(28), "cta": s(22), "ctax": s(44)},
    }


def render_html(slide: dict, out_path: str) -> str:
    """Render one slide dict to a PNG at out_path. Raises if Playwright/Chromium
    is unavailable (caller catches and falls back)."""
    from playwright.sync_api import sync_playwright  # lazy import

    w, h = slide["w"], slide["h"]
    html = _env.get_template("post.j2").render(**{**_typography(w, h), **slide})

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(
            viewport={"width": w, "height": h}, device_scale_factor=_DEVICE_SCALE
        )
        page.set_content(html, wait_until="load")
        page.screenshot(path=out_path, clip={"x": 0, "y": 0, "width": w, "height": h})
        browser.close()
    return out_path


def is_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        with sync_playwright() as p:
            # Probe that a browser is actually installed, not just the package.
            p.chromium.launch(args=["--no-sandbox"]).close()
        return True
    except Exception as e:  # noqa: BLE001
        log.info("Playwright/Chromium unavailable, using Pillow fallback (%s)", e)
        return False
