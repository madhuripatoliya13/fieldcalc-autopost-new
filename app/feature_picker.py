"""Picks the next (feature, angle) combination with zero repeats.

The marketing plan is intentionally feature-first:

1. Run the first angle across every feature.
2. Then run the second angle across every feature.
3. Continue until the full 15x7 matrix is complete.

Every generated post counts as a step, including rejected posts, so "Reject" +
"Create new post" moves forward instead of repeating the same creative.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app import generation_state
from app.database import Post, SessionLocal

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def load_features() -> list[dict]:
    return _load("features.json")["features"]


def load_angles() -> list[dict]:
    return _load("angles.json")["angles"]


def all_combinations() -> list[tuple[str, str]]:
    """The full universe of unique posts: 7 angles x 15 features = 105."""
    return [(f["id"], a["id"]) for a in load_angles() for f in load_features()]


def _seen_pairs(session) -> dict[tuple[str, str], int]:
    """Map each generated (feature, angle) -> most recent post id.

    Rejected posts are included. The user rejected that specific execution, but
    the planner should still advance to the next feature/angle instead of
    generating the same poster again.
    """
    rows = session.execute(
        select(Post.feature_id, Post.angle_id, func_max_id())
        .where(
            Post.pillar == "feature",
            Post.id > generation_state.reset_after_post_id(),
        )
        .group_by(Post.feature_id, Post.angle_id)
    ).all()
    return {(r[0], r[1]): r[2] for r in rows}


def func_max_id():
    from sqlalchemy import func

    return func.max(Post.id)


def _sequence_pick(combos: list[tuple[str, str]], seen: dict[tuple[str, str], int]) -> tuple[str, str]:
    """Pick the first unseen pair in the fixed feature-first sequence."""
    for combo in combos:
        if combo not in seen:
            return combo
    return combos[0]


def pick_next(epsilon: Optional[float] = None) -> Optional[dict]:
    """Return {'feature': {...}, 'angle': {...}} for the next post, or None.

    ``epsilon`` is retained for compatibility with earlier learning-loop tests,
    but the current product requirement is a fixed feature-first schedule.
    Performance insights should inform captions and creative direction, not jump
    the feature queue.
    """
    features = {f["id"]: f for f in load_features()}
    angles = {a["id"]: a for a in load_angles()}
    combos = all_combinations()

    with SessionLocal() as session:
        seen = _seen_pairs(session)

    feature_id, angle_id = _sequence_pick(combos, seen)
    return {"feature": features[feature_id], "angle": angles[angle_id]}


def coverage() -> dict:
    """Diagnostics for the dashboard: how much of the matrix is covered."""
    combos = all_combinations()
    with SessionLocal() as session:
        used = _seen_pairs(session)
    return {
        "total": len(combos),
        "used": len(used),
        "remaining": len(combos) - len(used),
    }
