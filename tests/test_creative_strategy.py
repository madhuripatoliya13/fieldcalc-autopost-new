from app import creative_strategy
from app.feature_picker import load_angles, load_features


def _by_id(items):
    return {item["id"]: item for item in items}


def test_strategy_includes_visual_audit_fields():
    features = _by_id(load_features())
    angles = _by_id(load_angles())

    strategy = creative_strategy.select(features["poi-markers"], angles["tutorial"])

    assert strategy["image_type_id"] == "tutorial_step"
    assert strategy["feature_motif"] == "saved location pins + named POI labels"
    assert strategy["asset_source"] in {
        "real app screenshot/enhanced scene asset",
        "real app screenshot",
    }
    assert strategy["variant_labels"] == [
        "Tutorial cards",
        "App UI showcase",
        "Cinematic scene",
        "Layered Android mockup",
        "Editorial grid",
    ]


def test_comparison_angle_keeps_problem_solution_image_type():
    features = _by_id(load_features())
    angles = _by_id(load_angles())

    strategy = creative_strategy.select(features["voice-navigation"], angles["comparison"])

    assert strategy["image_type_id"] == "problem_solution"
    assert strategy["feature_motif"] == "voice microphone + speech waves"
