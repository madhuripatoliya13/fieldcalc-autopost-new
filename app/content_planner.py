"""Decides WHAT to post today across content pillars, so the feed isn't 105 feature
ads in a row (the audit's biggest content gap). Returns a brief shaped exactly like
the feature pipeline expects — {feature, angle, pillar} — by synthesizing pseudo
feature/angle dicts for non-feature pillars. The rest of the pipeline is unchanged.

Selection:
  1. Seasonal override — if a calendar event is active AND not already posted this
     year, post it.
  2. Otherwise pick the pillar most under its long-run target weight over a trailing
     window, with the weekday theme as a soft bias.
  3. Pick concrete content within the pillar (feature picker for 'feature'; LRU over
     the pool / reviews for the rest).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select

from app import reviews, seasonal
from app.database import Post, PostStatus, SessionLocal
from app.feature_picker import load_angles, load_features, pick_next

log = logging.getLogger("autopost")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PILLARS = json.loads((DATA_DIR / "pillars.json").read_text(encoding="utf-8"))

TRAILING = 14  # window for measuring the current pillar mix


def _items_for(pillar: str) -> list[dict]:
    if pillar == "social_proof":
        return reviews.social_proof_items()
    return _PILLARS["pools"].get(pillar, [])


def _used_in_pillar(session, pillar: str) -> dict[str, int]:
    rows = session.execute(
        select(Post.feature_id, func.max(Post.id))
        .where(Post.pillar == pillar, Post.status != PostStatus.REJECTED)
        .group_by(Post.feature_id)
    ).all()
    return {r[0]: r[1] for r in rows}


def _lru_pick(session, pillar: str, items: list[dict]) -> Optional[dict]:
    if not items:
        return None
    used = _used_in_pillar(session, pillar)

    def key(it: dict):
        fid = f"{pillar}:{it['id']}"
        return (0, 0) if fid not in used else (1, used[fid])

    return sorted(items, key=key)[0]


def _choose_pillar(session, weekday: int) -> str:
    weights: dict = _PILLARS["weights"]
    recent = session.execute(
        select(Post.pillar)
        .where(Post.status != PostStatus.REJECTED)
        .order_by(Post.id.desc())
        .limit(TRAILING)
    ).scalars().all()
    total = len(recent) or 1
    deficits = {p: w - (recent.count(p) / total) for p, w in weights.items()}

    theme = _PILLARS["weekday_theme"].get(str(weekday))
    if theme in deficits:
        deficits[theme] += 0.05  # soft weekday nudge / tiebreak
    return max(deficits, key=deficits.get)


def _synthesize(pillar: str, item: dict) -> tuple[dict, dict]:
    feature_like = {
        "id": f"{pillar}:{item['id']}",
        "name": item["title"],
        "short": item.get("short", ""),
        "primary_benefit": item.get("benefit", ""),
        "keywords": item.get("keywords", []),
        "use_cases": item.get("points", []),
        "icon": item.get("icon", "map-pin"),
    }
    angle_like = {
        "id": pillar,
        "name": _PILLARS["display"].get(pillar, pillar.title()),
        "prompt_guidance": _PILLARS["guidance"].get(pillar, ""),
        "hook_style": item.get("hook_style", "relatable-problem"),
        "cta_style": item.get("cta", "Try it free — link in bio"),
        "best_formats": [_PILLARS["format"].get(pillar, "SINGLE")],
    }
    return feature_like, angle_like


def _seasonal_override(session, today: date) -> Optional[dict]:
    ev = seasonal.active_event(today)
    if not ev:
        return None
    fid = f"seasonal:{ev['id']}"
    # Don't repeat the same seasonal event within the same year.
    already = session.scalar(
        select(Post).where(
            Post.feature_id == fid,
            Post.status != PostStatus.REJECTED,
            Post.post_date.like(f"{today.year}-%"),
        )
    )
    if already:
        return None
    feature, angle = _synthesize("seasonal", ev)
    return {"feature": feature, "angle": angle, "pillar": "seasonal"}


def plan_today(today: Optional[date] = None) -> Optional[dict]:
    today = today or date.today()
    with SessionLocal() as session:
        seasonal_brief = _seasonal_override(session, today)
        if seasonal_brief:
            log.info("planner: seasonal override -> %s", seasonal_brief["feature"]["id"])
            return seasonal_brief

        pillar = _choose_pillar(session, today.weekday())

        if pillar == "feature":
            pick = pick_next()
            if pick:
                return {"feature": pick["feature"], "angle": pick["angle"], "pillar": "feature"}
            # Feature matrix exhausted mid-day — fall through to another pillar.
            pillar = "education"

        item = _lru_pick(session, pillar, _items_for(pillar))
        if not item:  # pool empty -> safe fallback to a feature post
            pick = pick_next()
            if not pick:
                return None
            return {"feature": pick["feature"], "angle": pick["angle"], "pillar": "feature"}

    feature, angle = _synthesize(pillar, item)
    log.info("planner: pillar=%s item=%s", pillar, item["id"])
    return {"feature": feature, "angle": angle, "pillar": pillar}


def plan_feature_post() -> Optional[dict]:
    """Manual dashboard drafts use the same LRU picker as the daily scheduler
    so all 15 features rotate, not just the 2 with curated realistic assets."""
    pick = pick_next()
    if not pick:
        return None
    return {"feature": pick["feature"], "angle": pick["angle"], "pillar": "feature"}
