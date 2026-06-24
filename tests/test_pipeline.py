"""State-machine + publishing guarantees, all in DRY_RUN (no Instagram contact)."""
from app import pipeline
from app.database import Post, PostStatus, SessionLocal


def _state(pid):
    with SessionLocal() as s:
        return s.get(Post, pid).status


def test_full_lifecycle_reaches_published():
    r = pipeline.generate_daily()
    assert r["status"] == "drafted"
    pid = r["post_id"]
    assert _state(pid) == PostStatus.PENDING_APPROVAL

    # Poller does nothing until approved.
    assert pipeline.publish_due()["published"] == []
    assert _state(pid) == PostStatus.PENDING_APPROVAL

    assert pipeline.approve(pid)["status"] == "approved"
    assert _state(pid) == PostStatus.APPROVED

    assert pid in pipeline.publish_due()["published"]
    assert _state(pid) == PostStatus.PUBLISHED


def test_one_post_per_day_idempotent():
    r1 = pipeline.generate_daily()
    r2 = pipeline.generate_daily()
    assert r2["status"] == "exists"
    assert r2["post_id"] == r1["post_id"]


def test_publish_is_idempotent_no_double_post():
    pid = pipeline.generate_daily()["post_id"]
    pipeline.approve(pid)
    first = pipeline.publish_due()
    second = pipeline.publish_due()
    assert pid in first["published"]
    assert second["published"] == []  # already PUBLISHED — never posts twice


def test_reject_sets_rejected():
    pid = pipeline.generate_daily()["post_id"]
    assert pipeline.reject(pid, "off-brand")["status"] == "rejected"
    assert _state(pid) == PostStatus.REJECTED
    # A rejected post is not publishable.
    pipeline.approve(pid)  # no-op: not pending
    assert _state(pid) == PostStatus.REJECTED


def test_rejected_post_allows_replacement_today():
    first = pipeline.generate_daily()["post_id"]
    pipeline.reject(first, "try a different creative")
    second = pipeline.generate_daily()
    assert second["status"] == "drafted"
    assert second["post_id"] != first
    assert _state(second["post_id"]) == PostStatus.PENDING_APPROVAL


def test_hashtags_capped_at_5():
    pid = pipeline.generate_daily()["post_id"]
    with SessionLocal() as s:
        post = s.get(Post, pid)
        assert post.hashtags is not None
        assert len(post.hashtags) <= 5  # C5: Instagram's 2025 cap


def test_generated_assets_exist():
    pid = pipeline.generate_daily()["post_id"]
    with SessionLocal() as s:
        post = s.get(Post, pid)
        assert post.image_urls and len(post.image_urls) >= 1
        assert post.utm_content == f"{post.feature_id}-{post.angle_id}-{pid}"


def test_regenerate_image_cycles_design_variant(monkeypatch):
    seen_variants = []

    def fake_generate_assets(feature, angle, post_format, post_key, content=None, strategy=None, use_realistic_asset=True):
        seen_variants.append((strategy or {}).get("visual_variant"))
        return [f"/tmp/{post_key}.png"]

    monkeypatch.setattr(pipeline.imaging, "generate_assets", fake_generate_assets)
    pid = pipeline.generate_daily()["post_id"]

    first = pipeline.regenerate_image(pid)
    second = pipeline.regenerate_image(pid)

    assert first["status"] == "image_regenerated"
    assert second["status"] == "image_regenerated"
    with SessionLocal() as s:
        post = s.get(Post, pid)
        traits = post.traits or {}
        strategy = traits.get("creative_strategy") or {}
        assert traits["image_regenerate_count"] == 2
        assert strategy["visual_variant"] == seen_variants[-1]
        assert strategy["feature_motif"]
        assert strategy["asset_source"]
        assert seen_variants[-1] != seen_variants[-2]
        assert traits["image_regeneration"]["status"] == "completed"
