"""Slide-spec builder (pure logic — no rendering, so the suite stays fast)."""
from PIL import Image

from app import imaging
from app.database import PostFormat
from app.feature_picker import load_angles, load_features
from app.imaging import _build_slides


def _ff():
    return load_features()[0], load_angles()[0]


def test_single_is_one_slide():
    f, a = _ff()
    slides = _build_slides(f, a, PostFormat.SINGLE, None)
    assert len(slides) == 1
    assert slides[0]["kind"] == "single"
    assert (slides[0]["w"], slides[0]["h"]) == (1080, 1080)


def test_story_is_vertical_with_sticker():
    f, a = _ff()
    slides = _build_slides(f, a, PostFormat.STORY, None)
    assert len(slides) == 1
    assert slides[0]["kind"] == "story"
    assert slides[0]["h"] == 1920


def test_carousel_is_multi_slide_hook_to_cta():
    f, a = _ff()
    slides = _build_slides(f, a, PostFormat.CAROUSEL, None)
    assert len(slides) >= 3
    assert slides[0]["kind"] == "hook"
    assert slides[-1]["kind"] == "cta"
    # dots metadata present on every slide
    assert all(s["total"] == len(slides) for s in slides)
    assert [s["index"] for s in slides] == list(range(len(slides)))


def test_content_keywords_become_chips():
    f, a = _ff()
    content = {"keywords": ["land measurement", "area calculator"], "cta": "Save this"}
    slides = _build_slides(f, a, PostFormat.SINGLE, content)
    assert slides[0]["chips"] == ["Land Measurement", "Area Calculator"]
    assert slides[0]["footer"] == "Save this"


def test_strategy_visual_variant_reaches_slide_spec():
    f, a = _ff()
    slides = _build_slides(
        f,
        a,
        PostFormat.SINGLE,
        None,
        strategy={
            "image_type_id": "tutorial_step",
            "image_type_label": "Tutorial Step",
            "visual_variant": 3,
        },
    )
    assert slides[0]["creative_image_type"] == "tutorial_step"
    assert slides[0]["forced_visual_variant"] == 3


def test_poi_overlay_does_not_fall_back_to_area_measure_polygon(monkeypatch):
    def fail_measure_overlay(*args, **kwargs):
        raise AssertionError("POI markers must not use the area/distance polygon overlay")

    monkeypatch.setattr(imaging, "_draw_measure_overlay", fail_measure_overlay)
    img = Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    imaging._draw_feature_symbol_overlay(
        img,
        {"feature_id": "poi-markers"},
        (20, 20, 420, 300),
        (255, 138, 61),
    )


def test_distinct_feature_overlays_call_dedicated_motifs(monkeypatch):
    calls = []

    monkeypatch.setattr(imaging, "_draw_voice_waves", lambda *args: calls.append("voice"))
    monkeypatch.setattr(imaging, "_draw_itinerary_cards", lambda *args: calls.append("itinerary"))
    monkeypatch.setattr(imaging, "_draw_record_stack", lambda *args: calls.append(args[3]))
    monkeypatch.setattr(imaging, "_draw_map_pin", lambda *args: calls.append("nearby-pin"))

    img = Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    box = (20, 20, 420, 300)
    accent = (47, 128, 237)
    for feature_id in ["voice-navigation", "route-planner", "nearby-location", "groups", "saved-measurements", "gps-gallery"]:
        imaging._draw_feature_symbol_overlay(img, {"feature_id": feature_id}, box, accent)

    assert "voice" in calls
    assert "itinerary" in calls
    assert calls.count("nearby-pin") == 3
    assert "Groups" in calls
    assert "Saved" in calls
    assert "Photos" in calls
