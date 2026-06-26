"""IG long-lived token auto-refresh (C-risk: 60-day expiry is a real failure mode).

A scheduled job calls maybe_refresh() ~daily. When the stored token is within
REFRESH_WINDOW_DAYS of expiry, it exchanges for a fresh 60-day token and saves it
to the DB (durable — survives redeploys). Never relies on a human "reminder".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.database import AppToken, SessionLocal

log = logging.getLogger("autopost")
settings = get_settings()

GRAPH = "https://graph.instagram.com"   # IG Login tokens use graph.instagram.com
REFRESH_WINDOW_DAYS = 12
TOKEN_NAME = "ig_long_lived"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def store_token(value: str, expires_in_seconds: int) -> None:
    expires_at = _now() + timedelta(seconds=expires_in_seconds)
    with SessionLocal() as s:
        tok = s.query(AppToken).filter_by(name=TOKEN_NAME).one_or_none()
        if tok is None:
            tok = AppToken(name=TOKEN_NAME)
            s.add(tok)
        tok.value = value
        tok.expires_at = expires_at
        tok.refreshed_at = _now()
        s.commit()
    log.info("stored IG token, expires %s", expires_at.isoformat())


def current_token() -> str:
    with SessionLocal() as s:
        tok = s.query(AppToken).filter_by(name=TOKEN_NAME).one_or_none()
        if tok and tok.value:
            return tok.value
    return settings.ig_access_token  # bootstrap from env on first run


def maybe_refresh() -> dict:
    """Refresh if within the window. Returns a small status dict for logging/alerts."""
    # DRY_RUN is a hard "touch nothing on Meta" switch: skip the refresh entirely so a
    # paused or action-blocked account never receives stray API calls (repeated blocked
    # calls can EXTEND a Meta action block). Publishing already honors dry_run in
    # instagram.py — this makes refresh consistent with it.
    if settings.dry_run:
        return {"refreshed": False, "reason": "dry_run — Meta calls disabled"}

    with SessionLocal() as s:
        tok = s.query(AppToken).filter_by(name=TOKEN_NAME).one_or_none()

    needs = tok is None or tok.expires_at is None or (
        tok.expires_at - _now() < timedelta(days=REFRESH_WINDOW_DAYS)
    )
    if not needs:
        return {"refreshed": False, "reason": "not due", "expires_at": tok.expires_at.isoformat()}

    # IG Login ("IGAA…") tokens refresh via ig_refresh_token grant.
    # Only the current token is needed — no client_id/secret.
    # Token must be at least 24h old and not yet expired.
    r = httpx.get(
        f"{GRAPH}/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": current_token(),
        },
        timeout=30,
    )
    if r.status_code >= 400:
        log.error("token refresh failed: %s", r.text)
        return {"refreshed": False, "error": r.text}
    body = r.json()
    store_token(body["access_token"], body.get("expires_in", 60 * 24 * 3600))
    return {"refreshed": True}
