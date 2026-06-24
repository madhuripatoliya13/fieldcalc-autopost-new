"""Creative strategy selector.

Chooses the audience region and image type for a post from the feature+angle pair.
The output is stored on Post.traits and shown in the approval dashboard so the
human reviewer can see why the bot chose a particular creative direction.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

FEATURE_MOTIFS = {
    "area-measurement": "land area polygon + acreage markers",
    "distance-tracking": "path distance line + measured segment labels",
    "poi-markers": "saved location pins + named POI labels",
    "route-planner": "multi-stop itinerary cards + route path",
    "voice-navigation": "voice microphone + speech waves",
    "find-routes": "route alternatives + map path",
    "speedometer": "speed gauge + road/HUD visual",
    "compass": "compass dial + direction landscape",
    "gps-camera": "GPS stamp card + camera proof visual",
    "gps-gallery": "photo stack + GPS metadata cards",
    "wonder-places": "landmark discovery + globe/explore motif",
    "street-view": "360 street-preview + road arrows",
    "groups": "grouped measurement cards + color stacks",
    "saved-measurements": "saved record cards + measurement history",
    "nearby-location": "nearby category pins + share-location card",
}

VARIANT_LABELS = [
    "Tutorial cards",
    "App UI showcase",
    "Cinematic scene",
    "Layered Android mockup",
    "Editorial grid",
]


def _load() -> dict:
    return json.loads((DATA_DIR / "creative_strategy.json").read_text(encoding="utf-8"))


def _visual_manifest() -> dict:
    try:
        return json.loads((DATA_DIR / "visual_assets.json").read_text(encoding="utf-8"))
    except Exception:
        return {"features": {}}


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


def _cycle_pick(values: list[str], seed: str) -> str:
    if not values:
        return "global"
    # Stable, deterministic, no random surprises in review.
    score = sum(ord(ch) for ch in seed)
    return values[score % len(values)]


def select(feature: dict, angle: dict, pillar: str = "feature") -> dict:
    config = _load()
    defaults = config["default_rules"]
    countries = _by_id(config["country_audiences"])
    image_types = _by_id(config["image_types"])
    feature_id = feature.get("id", "")
    angle_id = angle.get("id", "")
    override = config.get("feature_overrides", {}).get(feature_id, {})

    country_ids = override.get("audience_sequence") or defaults["audience_sequence"]
    country_id = _cycle_pick(country_ids, f"{feature_id}:{angle_id}:{pillar}")
    country = countries.get(country_id) or countries.get("global") or {
        "id": "global",
        "name": "Global",
        "visual_direction": "Region-neutral creative.",
        "share_hint": "default",
    }

    default_image_type = config["angle_image_type"].get(angle_id, "realistic_scenario")
    # The content angle is the primary creative promise. Feature preferences can
    # narrow risky choices later, but they should not turn a Comparison post into
    # a generic scenario or a Tip post into a standard mockup.
    image_type_id = default_image_type
    image_type = image_types.get(image_type_id) or image_types["realistic_scenario"]
    matching_screenshot_ready = bool(override.get("matching_screenshot_ready", False))
    if image_type.get("uses_app_mockup") and not matching_screenshot_ready:
        image_type_id = "realistic_scenario"
        image_type = image_types[image_type_id]

    trust_rule = image_type.get("trust_rule", defaults["trust_rule"])
    show_10m = trust_rule == "show" or (
        trust_rule == "major_posts_only"
        and angle_id in {"highlight", "use-case"}
        and feature_id in {"area-measurement", "route-planner", "voice-navigation", "gps-camera"}
    )
    visuals = _visual_manifest().get("features", {}).get(feature_id, {})
    if visuals.get("realistic_asset"):
        asset_source = "curated realistic asset + real app screenshot"
    elif visuals.get("scene_asset"):
        asset_source = "real app screenshot/enhanced scene asset"
    elif visuals.get("screenshot"):
        asset_source = "real app screenshot"
    else:
        asset_source = "generated vector scene"

    return {
        "country_id": country["id"],
        "country_name": country["name"],
        "country_reason": country.get("share_hint", ""),
        "visual_direction": country.get("visual_direction", ""),
        "image_type_id": image_type["id"],
        "image_type_label": image_type["label"],
        "image_type_description": image_type["description"],
        "uses_app_mockup": bool(image_type.get("uses_app_mockup")),
        "mockup_device": defaults["mockup_device"],
        "mockup_policy": defaults["mockup_policy"],
        "logo_rule": image_type.get("logo_rule", defaults["logo_rule"]),
        "trust_rule": trust_rule,
        "show_10m": show_10m,
        "language": defaults["language"],
        "feature_motif": FEATURE_MOTIFS.get(feature_id, "feature-specific vector scene"),
        "asset_source": asset_source,
        "variant_labels": VARIANT_LABELS,
        "selection_reason": (
            f"Feature '{feature.get('name', feature_id)}' with angle "
            f"'{angle.get('name', angle_id)}' maps to {image_type['label']} "
            f"for {country['name']} audience."
        ),
        "available_image_types": [
            {
                "id": it["id"],
                "label": it["label"],
                "description": it["description"],
                "uses_app_mockup": bool(it.get("uses_app_mockup")),
            }
            for it in config["image_types"]
        ],
    }
