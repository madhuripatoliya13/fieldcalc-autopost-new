"""Approval dashboard UI + ops endpoints (FastAPI TestClient, DRY_RUN)."""
from fastapi.testclient import TestClient

from app import ops
from app.config import get_settings
from app.database import Post, PostStatus, SessionLocal
from app.main import app

client = TestClient(app)
AUTH = ("admin", "change-me")  # matches default DASHBOARD_PASSWORD in tests


def _make_pending() -> int:
    from app import pipeline
    return pipeline.generate_daily()["post_id"]


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["dry_run"] is True


def test_dashboard_requires_password():
    assert client.get("/").status_code == 401


def test_dashboard_lists_pending_post():
    _make_pending()
    r = client.get("/", auth=AUTH)
    assert r.status_code == 200
    assert "Approve" in r.text
    assert "Create new post" in r.text
    assert any(label in r.text for label in ("AI generated", "Curated realistic asset", "Enhanced poster", "App screenshots"))


def test_dashboard_backfills_missing_creative_audit_fields():
    pid = _make_pending()
    with SessionLocal() as s:
        post = s.get(Post, pid)
        traits = dict(post.traits or {})
        old_strategy = dict(traits.get("creative_strategy") or {})
        old_strategy.pop("feature_motif", None)
        old_strategy.pop("asset_source", None)
        old_strategy.pop("variant_labels", None)
        traits["creative_strategy"] = old_strategy
        post.traits = traits
        s.commit()

    r = client.get("/", auth=AUTH)

    assert r.status_code == 200
    assert "Feature motif" in r.text
    assert "Asset source" in r.text
    assert "land area polygon" in r.text


def test_ui_approve_transitions_state():
    pid = _make_pending()
    r = client.post(f"/ui/approve/{pid}", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert "approved" in r.headers["location"]
    with SessionLocal() as s:
        assert s.get(Post, pid).status == PostStatus.PUBLISHED
    page = client.get(r.headers["location"], auth=AUTH)
    assert "Recent activity" in page.text
    assert "PUBLISHED" in page.text


def test_ui_reject_transitions_state():
    pid = _make_pending()
    client.post(f"/ui/reject/{pid}", auth=AUTH, data={"reason": "off-brand"}, follow_redirects=False)
    with SessionLocal() as s:
        assert s.get(Post, pid).status == PostStatus.REJECTED


def test_ui_create_post_after_reject():
    pid = _make_pending()
    client.post(f"/ui/reject/{pid}", auth=AUTH, data={"reason": "off-brand"}, follow_redirects=False)
    r = client.post("/ui/create-post", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        pending = s.query(Post).filter(Post.status == PostStatus.PENDING_APPROVAL).all()
        assert len(pending) == 1
        assert pending[0].id != pid


def test_ui_create_post_after_approve():
    pid = _make_pending()
    client.post(f"/ui/approve/{pid}", auth=AUTH, follow_redirects=False)
    r = client.post("/ui/create-post", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        approved = s.get(Post, pid)
        pending = s.query(Post).filter(Post.status == PostStatus.PENDING_APPROVAL).all()
        assert approved.status == PostStatus.PUBLISHED
        assert len(pending) == 1
        assert pending[0].id != pid


def test_ui_edit_updates_caption():
    pid = _make_pending()
    client.post(f"/ui/edit/{pid}", auth=AUTH,
                data={"caption": "Brand new caption. Save this.", "hashtags": "FieldCalc, GPS"},
                follow_redirects=False)
    with SessionLocal() as s:
        p = s.get(Post, pid)
        assert p.caption == "Brand new caption. Save this."
        assert p.hashtags == ["#FieldCalc", "#GPS"]


def test_preflight_ok_in_dry_run():
    assert ops.preflight()["ok"] is True


def test_cron_run_daily_accepts_background_job(monkeypatch):
    calls = []

    def fake_daily_background():
        calls.append("ran")

    from app import main

    monkeypatch.setattr(main, "_run_daily_background", fake_daily_background)
    r = client.post("/cron/run-daily", headers={"X-Run-Token": get_settings().run_token})

    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    assert calls == ["ran"]


def test_plan_page_shows_feature_angle_matrix():
    _make_pending()
    r = client.get("/plan", auth=AUTH)
    assert r.status_code == 200
    assert "Feature post plan" in r.text
    assert "GPS Area Measurement" in r.text
    assert "Tutorial / How-To" in r.text
    assert "Realistic ready" in r.text


def test_reset_generation_plan_route():
    _make_pending()
    r = client.post("/ui/reset-generation", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert "Generation%20plan%20reset" in r.headers["location"]
    page = client.get("/plan", auth=AUTH)
    assert "Current plan reset starts after post" in page.text
