"""Play Store review mining -> ANONYMOUS social-proof content.

To respect user privacy and platform ToS (the audit's completeness critic flagged
republishing user names/likeness), we deliberately DROP all author names and keep
only the rating + text of high-rated, reasonable-length reviews. If scraping is
unavailable or returns nothing, social proof falls back to generic items so the
pillar always has content.

Scraping runs occasionally (cached to data/reviews_cache.json), never on the daily
hot path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("autopost")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA_DIR / "reviews_cache.json"


def _app_id() -> str:
    app = json.loads((DATA_DIR / "features.json").read_text())["app"]
    return app["play_store_id"]


def fetch_and_cache(limit: int = 40) -> int:
    """Best-effort scrape. Returns count cached. Safe to call rarely / manually."""
    try:
        from google_play_scraper import Sort, reviews  # lazy

        result, _ = reviews(
            _app_id(), lang="en", country="us", sort=Sort.MOST_RELEVANT, count=limit
        )
    except Exception as e:  # noqa: BLE001
        log.info("review scrape unavailable (%s)", e)
        return 0

    kept = []
    for r in result:
        text = (r.get("content") or "").strip()
        if r.get("score", 0) >= 4 and 40 <= len(text) <= 180 and "http" not in text.lower():
            kept.append({"score": int(r["score"]), "text": text})  # NO name stored
    CACHE.write_text(json.dumps(kept, indent=2))
    log.info("cached %d anonymized reviews", len(kept))
    return len(kept)


def _load_cached() -> list[dict]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def social_proof_items() -> list[dict]:
    """Content items for the social_proof pillar. Anonymous quote cards from cached
    reviews, or generic fallback items when none are available."""
    cached = _load_cached()
    items = []
    for i, r in enumerate(cached):
        items.append({
            "id": f"review-{i}",
            "title": "★" * r["score"] + " from a real user",
            "short": f"“{r['text']}”",
            "benefit": "Join thousands who measure smarter with FieldCalc.",
            "keywords": ["fieldcalc reviews", "trusted app"],
            "points": [],
            "icon": "save",
            "cta": "Join them — link in bio",
            "hook_style": "social-proof",
        })
    if items:
        return items
    # Fallback: generic social proof (defined in pillars.json).
    pillars = json.loads((DATA_DIR / "pillars.json").read_text())
    return pillars.get("social_proof_fallback", [])
