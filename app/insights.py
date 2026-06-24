"""Pull Instagram post performance back into the DB (Sprint 6 ingestion).

Metrics mature over days, so we snapshot each post at ~T+24h and ~T+72h. We use the
2026-correct metric names — the old `impressions`/`profile_views`/`website_clicks`
were deprecated in Graph API v21 and return nothing.

DRY_RUN produces deterministic mock metrics (varying by post) so the whole learning
loop is testable with no Instagram connection.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.database import Post, PostMetric, PostStatus, SessionLocal
from app.retries import TransientError, resilient

log = logging.getLogger("autopost")
settings = get_settings()

GRAPH = "https://graph.facebook.com/v21.0"
METRICS = "reach,views,saved,shares,total_interactions"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mock_metrics(seed: str) -> dict:
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    reach = 300 + h % 700
    saved = h % 45
    shares = (h >> 5) % 30
    ti = saved + shares + (h >> 9) % 130
    return {
        "reach": reach,
        "views": reach + (h >> 13) % 600,
        "saved": saved,
        "shares": shares,
        "total_interactions": ti,
        "bio_link_clicked": (h >> 17) % 35,
        "profile_visits": (h >> 21) % 70,
    }


def follower_count() -> int:
    if settings.dry_run:
        return 1000
    try:
        r = httpx.get(
            f"{GRAPH}/{settings.ig_user_id}",
            params={"fields": "followers_count", "access_token": settings.ig_access_token},
            timeout=30,
        )
        return int(r.json().get("followers_count", 0))
    except Exception:  # noqa: BLE001
        return 0


@resilient()
def fetch_post_insights(media_id: str) -> dict:
    if settings.dry_run or not media_id:
        return _mock_metrics(media_id or "x")
    try:
        r = httpx.get(
            f"{GRAPH}/{media_id}/insights",
            params={"metric": METRICS, "access_token": settings.ig_access_token},
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise TransientError(f"insights net: {e}") from e
    if r.status_code >= 500 or r.status_code == 429:
        raise TransientError(f"{r.status_code}: {r.text}")
    out: dict = {}
    for m in r.json().get("data", []):
        vals = m.get("values") or [{}]
        out[m["name"]] = vals[0].get("value", 0)
    return out


def capture_due() -> dict:
    """Snapshot PUBLISHED posts at ~T+24h (0 snapshots yet) and ~T+72h (1 snapshot)."""
    captured = []
    with SessionLocal() as s:
        posts = s.query(Post).filter(
            Post.status == PostStatus.PUBLISHED, Post.posted_at.isnot(None)
        ).all()
        targets = []
        for p in posts:
            posted = p.posted_at
            if posted.tzinfo is None:  # SQLite returns naive; treat stored time as UTC
                posted = posted.replace(tzinfo=timezone.utc)
            age = _now() - posted
            n = len(p.metrics)
            if (age >= timedelta(hours=24) and n == 0) or (age >= timedelta(hours=72) and n == 1):
                targets.append((p.id, p.ig_media_id))

    fcount = follower_count()
    for pid, media_id in targets:
        m = fetch_post_insights(media_id)
        with SessionLocal() as s:
            s.add(PostMetric(post_id=pid, follower_count_at_capture=fcount, **m))
            s.commit()
        captured.append(pid)
    log.info("insights captured for %d post(s)", len(captured))
    return {"captured": captured}
