import io

from PIL import Image

from app.image_gen import build_scenario_prompt, create_marketing_composite


def _background_bytes() -> bytes:
    img = Image.new("RGB", (1080, 1080), (80, 120, 95))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _feature():
    return {
        "id": "find-routes",
        "name": "Find Routes",
        "primary_benefit": "Pick the best of several routes and save the ones you reuse.",
        "short": "Get directions with route alternatives, history, and favorites.",
        "keywords": ["directions", "route finder"],
        "use_cases": ["compare route options", "save a daily commute"],
    }


def test_marketing_composite_has_no_bottom_download_button():
    for variant in range(5):
        img = create_marketing_composite(
            _background_bytes(),
            {
                "accent": "#2f80ed",
                "app_name": "FieldCalc",
                "eyebrow": "Feature Highlight",
                "forced_visual_variant": variant,
            },
            feature=_feature(),
            content={"keywords": ["directions", "route finder"]},
        )
        assert img.size == (1080, 1080)
        # Old template had a lime Google Play button centered near the bottom.
        r, g, b = img.getpixel((540, 885))
        assert not (r > 170 and g > 200 and b < 80)


def test_only_selected_variant_uses_app_mockup_area():
    # Variant 0 is cinematic/no-phone: center should remain scene/overlay, not a
    # large blue placeholder phone screen.
    img = create_marketing_composite(
        _background_bytes(),
        {
            "accent": "#2f80ed",
            "app_name": "FieldCalc",
            "eyebrow": "Feature Highlight",
            "forced_visual_variant": 0,
        },
        feature=_feature(),
        content={},
    )
    r, g, b = img.getpixel((540, 540))
    assert not (r < 80 and g > 120 and b > 180)


def test_missing_screenshot_does_not_render_blank_phone():
    img = create_marketing_composite(
        _background_bytes(),
        {
            "accent": "#2f80ed",
            "app_name": "FieldCalc",
            "eyebrow": "Tutorial / How-To",
            "forced_visual_variant": 3,
            "screenshot_path": "/path/that/does/not/exist.png",
        },
        feature={**_feature(), "id": "distance-tracking", "name": "Distance Measurement"},
        content={},
    )

    # The old behavior drew a large solid blue placeholder screen in this area.
    sample_points = [(820, 430), (850, 520), (880, 610)]
    blue_placeholder_pixels = 0
    for point in sample_points:
        r, g, b = img.getpixel(point)
        if r < 80 and g > 110 and b > 170:
            blue_placeholder_pixels += 1
    assert blue_placeholder_pixels < 2


def test_people_prompts_avoid_visible_faces():
    prompt = build_scenario_prompt(
        {"id": "gps-camera", "name": "GPS Stamp Camera"},
        {"name": "Tutorial / How-To"},
        {},
    ).lower()

    assert "face not visible" in prompt or "no visible face" in prompt
    assert "no distorted face" in prompt
