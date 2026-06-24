"""UTM links + install attribution joins."""
from app import attribution, links
from app.database import Post, PostStatus, SessionLocal


def test_play_referrer_url_encodes_utm_content():
    url = links.play_referrer_url("area-measurement-tutorial-12", pillar="feature")
    assert "referrer=" in url
    # The inner '&' between utm params must be percent-encoded inside the referrer value.
    assert "utm_content%3Darea-measurement-tutorial-12" in url
    assert "%26" in url  # encoded & between params


def test_short_link_falls_back_to_full_url_without_shortener(monkeypatch):
    monkeypatch.setattr(links.settings, "public_asset_base_url", "")
    assert links.short_link("x-y-1").startswith(links.settings.play_store_url[:20])


def test_record_and_sum_installs():
    attribution.record_install("feat-a-1", "play_console", 3)
    attribution.record_install("feat-a-1", "revenuecat", 2)
    assert attribution.installs_for("feat-a-1") == 5
    assert attribution.installs_for("nope") == 0


def test_installs_join_back_to_post():
    with SessionLocal() as s:
        s.add(Post(post_date="2026-01-01", pillar="feature", feature_id="area-measurement",
                   angle_id="tutorial", utm_content="area-measurement-tutorial-1",
                   status=PostStatus.PUBLISHED))
        s.commit()
    attribution.record_install("area-measurement-tutorial-1", "play_console", 7)
    rows = attribution.installs_by_post()
    target = [r for r in rows if r["utm_content"] == "area-measurement-tutorial-1"]
    assert target and target[0]["installs"] == 7
    assert target[0]["feature"] == "area-measurement"


def test_extract_utm_content_from_referrer_string():
    raw = "utm_source=instagram&utm_medium=social&utm_content=poi-markers-faq-9"
    assert attribution._extract_utm_content(raw) == "poi-markers-faq-9"
    assert attribution._extract_utm_content("poi-markers-faq-9") == "poi-markers-faq-9"
