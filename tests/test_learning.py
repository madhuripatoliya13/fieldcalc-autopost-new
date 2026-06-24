"""Learning loop: insights capture, scoring, winning patterns, and digest."""
from datetime import datetime, timedelta, timezone

from app import attribution, digest, feature_picker, insights, learning
from app.database import Post, PostFormat, PostMetric, PostStatus, SessionLocal


def _published(pid_date, feature, angle, utm, posted_hours_ago=25, pillar="feature"):
    with SessionLocal() as s:
        s.add(Post(
            post_date=pid_date, pillar=pillar, feature_id=feature, angle_id=angle,
            format=PostFormat.SINGLE, status=PostStatus.PUBLISHED,
            ig_media_id=f"media-{utm}", utm_content=utm,
            posted_at=datetime.now(timezone.utc) - timedelta(hours=posted_hours_ago),
            traits={"hook_style": "outcome-promise", "cta_style": "Save this", "keywords": ["land measurement"]},
        ))
        s.commit()
        return s.query(Post).filter_by(utm_content=utm).one().id


def test_capture_due_snapshots_after_24h():
    pid = _published("2026-01-01", "area-measurement", "tutorial", "am-t-1")
    res = insights.capture_due()
    assert pid in res["captured"]
    with SessionLocal() as s:
        p = s.get(Post, pid)
        assert len(p.metrics) == 1
        assert p.metrics[0].reach and p.metrics[0].follower_count_at_capture == 1000


def test_capture_skips_fresh_posts():
    _published("2026-01-02", "distance-tracking", "tutorial", "dt-t-1", posted_hours_ago=2)
    assert insights.capture_due()["captured"] == []


def test_scoring_and_winning_patterns():
    _published("2026-01-03", "area-measurement", "tutorial", "am-t-9")
    _published("2026-01-04", "poi-markers", "faq", "pm-f-9")
    insights.capture_due()
    attribution.record_install("am-t-9", "play_console", 50)  # make this one a clear winner
    ranked = learning.scored_posts()
    assert len(ranked) == 2
    assert ranked[0]["score"] >= ranked[1]["score"]
    patterns = learning.winning_patterns()
    assert "hook_styles" in patterns and patterns["sample_size"] == 2


def test_bandit_cold_start_is_lru():
    # No metrics yet -> deterministic LRU first pick.
    p = feature_picker.pick_next()
    assert p["feature"]["id"] == "area-measurement" and p["angle"]["id"] == "tutorial"


def test_performance_data_does_not_jump_feature_queue():
    # Seed a measured, high-install combo. The planner should still follow the
    # fixed feature-first campaign schedule.
    _published("2026-02-01", "speedometer", "highlight", "sp-h-1")
    insights.capture_due()
    attribution.record_install("sp-h-1", "play_console", 999)
    pick = feature_picker.pick_next(epsilon=0.0)
    assert (pick["feature"]["id"], pick["angle"]["id"]) == ("area-measurement", "tutorial")


def test_performance_data_still_feeds_winning_patterns():
    _published("2026-02-02", "compass", "highlight", "cm-h-1")
    insights.capture_due()
    attribution.record_install("cm-h-1", "play_console", 999)
    patterns = learning.winning_patterns()
    assert patterns["sample_size"] == 1
    assert patterns["hook_styles"]


def test_digest_builds_without_crashing():
    _published("2026-03-01", "compass", "tip-trick", "cm-tt-1")
    insights.capture_due()
    out = digest.build_digest()
    assert "Weekly digest" in out
