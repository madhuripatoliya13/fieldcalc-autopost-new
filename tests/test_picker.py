"""Picker guarantees: full 105 matrix, and zero repeats until it's exhausted."""
from app import generation_state
from app.database import Post, PostFormat, PostStatus, SessionLocal
from app.feature_picker import all_combinations, coverage, pick_next


def test_matrix_is_15x7():
    combos = all_combinations()
    assert len(combos) == 105
    assert len(set(combos)) == 105  # all unique


def test_first_pick_is_deterministic_on_empty_db():
    pick = pick_next()
    assert pick["feature"]["id"]
    assert pick["angle"]["id"]


def test_feature_first_sequence_before_next_angle():
    expected = ["area-measurement", "distance-tracking", "poi-markers"]
    for day, feature_id in enumerate(expected):
        pick = pick_next()
        assert pick["feature"]["id"] == feature_id
        assert pick["angle"]["id"] == "tutorial"
        with SessionLocal() as s:
            s.add(
                Post(
                    post_date=f"day-{day:03d}",
                    feature_id=pick["feature"]["id"],
                    angle_id=pick["angle"]["id"],
                    format=PostFormat.SINGLE,
                    status=PostStatus.REJECTED,
                    pillar="feature",
                )
            )
            s.commit()


def test_rejected_full_first_angle_moves_to_second_angle():
    feature_ids = [feature_id for feature_id, angle_id in all_combinations() if angle_id == "tutorial"]
    for day, feature_id in enumerate(feature_ids):
        with SessionLocal() as s:
            s.add(
                Post(
                    post_date=f"day-{day:03d}",
                    feature_id=feature_id,
                    angle_id="tutorial",
                    format=PostFormat.SINGLE,
                    status=PostStatus.REJECTED,
                    pillar="feature",
                )
            )
            s.commit()

    pick = pick_next()
    assert pick["feature"]["id"] == "area-measurement"
    assert pick["angle"]["id"] == "highlight"


def test_reset_starts_feature_sequence_again():
    with SessionLocal() as s:
        s.add(
            Post(
                post_date="day-000",
                feature_id="area-measurement",
                angle_id="tutorial",
                format=PostFormat.SINGLE,
                status=PostStatus.PUBLISHED,
                pillar="feature",
            )
        )
        s.commit()
        generation_state.set_reset_after(1)
    pick = pick_next()
    assert pick["feature"]["id"] == "area-measurement"
    assert pick["angle"]["id"] == "tutorial"


def test_no_repeat_until_full_cycle():
    """Walk the entire matrix marking each pick as published; every pick must be
    unique across all 105, proving the no-duplicate guarantee."""
    seen = set()
    for day in range(105):
        pick = pick_next()
        key = (pick["feature"]["id"], pick["angle"]["id"])
        assert key not in seen, f"REPEAT at step {day}: {key}"
        seen.add(key)
        with SessionLocal() as s:
            # unique post_date per row to satisfy the daily guard
            s.add(
                Post(
                    post_date=f"day-{day:03d}",
                    feature_id=key[0],
                    angle_id=key[1],
                    format=PostFormat.SINGLE,
                    status=PostStatus.PUBLISHED,
                )
            )
            s.commit()
    assert len(seen) == 105
    assert coverage()["remaining"] == 0
