"""Content-pillar planner: seasonal override, weighted mix, no-repeat, synthesis."""
from datetime import date

from app import content_planner, seasonal
from app.database import Post, PostFormat, PostStatus, SessionLocal


def _save(brief, day):
    with SessionLocal() as s:
        s.add(Post(post_date=day, pillar=brief["pillar"],
                   feature_id=brief["feature"]["id"], angle_id=brief["angle"]["id"],
                   format=PostFormat.SINGLE, status=PostStatus.PUBLISHED))
        s.commit()


def test_brief_shape_is_pipeline_compatible():
    b = content_planner.plan_today(date(2026, 6, 19))  # no seasonal event
    assert {"feature", "angle", "pillar"} <= b.keys()
    f, a = b["feature"], b["angle"]
    assert f["id"] and f["name"] and "keywords" in f
    assert a["id"] and a["name"] and "best_formats" in a


def test_seasonal_event_lookup():
    assert seasonal.active_event(date(2026, 4, 22))["id"] == "earth-day"
    assert seasonal.active_event(date(2026, 11, 18))["id"] == "gis-day"
    assert seasonal.active_event(date(2026, 6, 19)) is None


def test_seasonal_override_then_not_repeated_same_year():
    b1 = content_planner.plan_today(date(2026, 4, 22))
    assert b1["pillar"] == "seasonal" and b1["feature"]["id"] == "seasonal:earth-day"
    _save(b1, "2026-04-22")
    # Same event, same year -> must NOT override again.
    b2 = content_planner.plan_today(date(2026, 4, 23))
    assert not (b2["pillar"] == "seasonal" and b2["feature"]["id"] == "seasonal:earth-day")


def test_long_run_mix_is_diverse_not_all_feature():
    """Over many days the planner must use several pillars, not only features."""
    seen = set()
    d = date(2026, 6, 1)  # June: no seasonal windows here except none on these days
    from datetime import timedelta
    for i in range(40):
        day = d + timedelta(days=i)
        b = content_planner.plan_today(day)
        seen.add(b["pillar"])
        _save(b, day.isoformat())
    assert len(seen) >= 3  # genuine variety
    assert "feature" in seen


def test_no_repeat_within_education_pillar():
    picks = []
    from datetime import timedelta
    d = date(2026, 7, 1)
    # Force education repeatedly by saving education and checking distinct items.
    for i in range(3):
        b = content_planner.plan_today(d + timedelta(days=i))
        if b["pillar"] == "education":
            picks.append(b["feature"]["id"])
        _save(b, (d + timedelta(days=i)).isoformat())
    assert len(picks) == len(set(picks))  # no education item repeats


def test_social_proof_fallback_when_no_reviews():
    items = content_planner._items_for("social_proof")
    assert items, "social_proof must always have content (fallback)"
    assert items[0]["id"]
