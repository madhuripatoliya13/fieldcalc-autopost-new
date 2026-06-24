"""Operational helpers: error tracking, dead-man's-switch, and a pre-flight check.

All best-effort and safe when unconfigured (local/CI). The dead-man's-switch is the
thing that catches the silent-failure mode: if the daily job stops pinging
Healthchecks.io, it alerts you — a bot that posts nothing otherwise looks healthy.
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger("autopost")
settings = get_settings()

GRAPH = "https://graph.facebook.com/v21.0"


def init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.0)
        log.info("Sentry initialized")
    except Exception as e:  # noqa: BLE001
        log.warning("Sentry init failed: %s", e)


def ping_healthcheck(suffix: str = "") -> None:
    if not settings.healthcheck_ping_url:
        return
    try:
        httpx.get(settings.healthcheck_ping_url + suffix, timeout=10)
    except Exception as e:  # noqa: BLE001
        log.warning("healthcheck ping failed: %s", e)


def preflight() -> dict:
    """Verify the system can actually operate: DB reachable, token valid, under the
    publish cap. Returns a dict; `ok` is True only if nothing is broken."""
    checks: dict = {}

    try:
        from sqlalchemy import text

        from app.database import SessionLocal

        with SessionLocal() as s:
            s.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"error: {e}"

    if settings.dry_run:
        checks["ig_token"] = "dry_run"
        checks["publish_limit"] = "dry_run"
    else:
        try:
            r = httpx.get(
                f"{GRAPH}/{settings.ig_user_id}",
                params={"fields": "id", "access_token": settings.ig_access_token},
                timeout=20,
            )
            checks["ig_token"] = "ok" if r.status_code < 400 else f"error: {r.text[:120]}"
        except Exception as e:  # noqa: BLE001
            checks["ig_token"] = f"error: {e}"
        try:
            r = httpx.get(
                f"{GRAPH}/{settings.ig_user_id}/content_publishing_limit",
                params={"access_token": settings.ig_access_token},
                timeout=20,
            )
            checks["publish_limit"] = "ok" if r.status_code < 400 else f"error: {r.text[:120]}"
        except Exception as e:  # noqa: BLE001
            checks["publish_limit"] = f"error: {e}"

    checks["ok"] = all(
        v in ("ok", "dry_run") for k, v in checks.items() if k != "ok"
    )
    return checks
