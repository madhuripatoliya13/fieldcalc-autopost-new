"""FastAPI entrypoint.

Exposes:
  GET  /health      — liveness probe (also pings Healthchecks.io).
  POST /run-daily   — token-protected external-cron trigger (C2 fix). An external
                      scheduler (cron-job.org / GitHub Actions) hits this daily; it
                      wakes the sleeping host AND kicks off generation. APScheduler
                      stays only as an in-process safety net.
  POST /run-poller  — token-protected; publishes APPROVED posts whose publish_at is due.

The actual generation/publish pipelines are filled in across Sprints 1-3; this
Sprint 0 skeleton wires the durable trigger surface and DB bootstrap.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from app import attribution, backup, dashboard, digest, insights, learning, ops, pipeline, token_refresh
from app.config import get_settings
from app.database import init_db
from app.feature_picker import coverage
from app.imaging import OUT_DIR

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ops.init_sentry()
    init_db()
    yield


app = FastAPI(title="FieldCalc IG Auto-Post", version="0.1.0", lifespan=lifespan)

# Serve generated images for the dashboard preview.
app.mount("/generated", StaticFiles(directory=str(OUT_DIR)), name="generated")
# Human-approval dashboard UI (password-protected inside).
app.include_router(dashboard.router)


def require_run_token(x_run_token: str = Header(default="")) -> None:
    if x_run_token != settings.run_token:
        raise HTTPException(status_code=401, detail="invalid run token")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
        "db": "postgres" if settings.is_postgres else "sqlite",
        "coverage": coverage(),
    }


@app.get("/debug/token", dependencies=[Depends(require_run_token)])
def debug_token() -> dict:
    """Diagnostics: validate the IG token and read the content-publishing quota
    directly from Meta (Render can reach Meta even when the office network can't)."""
    import certifi
    import requests
    base = settings.graph_base_url
    tok = settings.ig_access_token
    uid = settings.ig_user_id
    out: dict = {}
    try:
        r = requests.get(f"{base}/me", params={"fields": "id,username,account_type", "access_token": tok}, timeout=30, verify=certifi.where())
        out["me"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]}
    except Exception as e:  # noqa: BLE001
        out["me"] = {"error": str(e)}
    try:
        r = requests.get(f"{base}/{uid}/content_publishing_limit", params={"fields": "quota_usage,config", "access_token": tok}, timeout=30, verify=certifi.where())
        out["publishing_limit"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]}
    except Exception as e:  # noqa: BLE001
        out["publishing_limit"] = {"error": str(e)}
    return out


@app.post("/run-daily", dependencies=[Depends(require_run_token)])
def run_daily() -> dict:
    """External cron hits this daily: refresh token if due, then draft today's post."""
    token_refresh.maybe_refresh()
    result = pipeline.generate_daily()
    ops.ping_healthcheck()  # dead-man's-switch: prove the daily job ran
    return result


@app.get("/preflight", dependencies=[Depends(require_run_token)])
def preflight() -> dict:
    """DB reachable? token valid? under the publish cap?"""
    return ops.preflight()


@app.post("/run-backup", dependencies=[Depends(require_run_token)])
def run_backup() -> dict:
    """External cron (daily): export the DB to a JSON backup."""
    return {"status": "ok", "path": backup.export_json()}


@app.post("/run-poller", dependencies=[Depends(require_run_token)])
def run_poller() -> dict:
    """External cron hits this every ~5 min: publish APPROVED posts that are due."""
    return pipeline.publish_due()


@app.post("/approve/{post_id}", dependencies=[Depends(require_run_token)])
def approve(post_id: int) -> dict:
    """Human approval (dashboard wires a real UI to this in Sprint 4)."""
    return pipeline.approve(post_id)


@app.post("/reject/{post_id}", dependencies=[Depends(require_run_token)])
def reject(post_id: int, reason: str = "") -> dict:
    return pipeline.reject(post_id, reason)


@app.post("/requeue/{post_id}", dependencies=[Depends(require_run_token)])
def requeue(post_id: int) -> dict:
    """Reset a FAILED post back to APPROVED so the poller retries it with a fresh
    container (clears the stale creation_id from the failed attempt)."""
    from datetime import datetime, timezone

    from app.database import Post, PostStatus, SessionLocal
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post:
            return {"status": "error", "reason": "not found"}
        post.status = PostStatus.APPROVED
        post.creation_id = None
        post.reject_reason = None
        post.publish_at = datetime.now(timezone.utc)
        s.commit()
    return {"status": "requeued", "post_id": post_id}


@app.post("/ingest/installs", dependencies=[Depends(require_run_token)])
def ingest_install(utm_content: str, source: str = "revenuecat", count: int = 1) -> dict:
    """Called by a RevenueCat webhook or a manual/Play import to record an install
    attributed to a post's utm_content."""
    attribution.record_install(utm_content, source, count)
    return {"status": "ok", "utm_content": utm_content, "count": count}


@app.get("/insights/attribution", dependencies=[Depends(require_run_token)])
def attribution_view() -> dict:
    """Installs joined to posts — which feature+angle drives installs."""
    rows = attribution.installs_by_post()
    return {"posts": rows, "total_installs": sum(r["installs"] for r in rows)}


@app.post("/run-insights", dependencies=[Depends(require_run_token)])
def run_insights() -> dict:
    """External cron (daily): snapshot post metrics at T+24h / T+72h."""
    return insights.capture_due()


@app.get("/insights/performance", dependencies=[Depends(require_run_token)])
def performance() -> dict:
    """Ranked posts by performance + current winning patterns."""
    return {"ranked": learning.scored_posts(), "winning_patterns": learning.winning_patterns()}


@app.post("/run-digest", dependencies=[Depends(require_run_token)])
def run_digest(week_of: str) -> dict:
    """External cron (weekly): compute learnings + send the digest."""
    return digest.send_weekly_digest(week_of)
