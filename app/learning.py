"""The learning loop (Sprint 6).

Turns captured metrics + installs into:
  - a per-post performance_score (normalized so the loop learns "post better", not
    "post when we had more followers"),
  - combo_scores() for the bandit picker,
  - winning_patterns() injected back into caption/image prompts,
  - compute_weekly_learnings() which persists the week's winning DNA.

Scoring blends install-rate (heaviest — installs are the goal), save-rate, and
engagement-rate, each min-max normalized across the population of measured posts.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from sqlalchemy import func, select

from app.database import Install, Learning, Post, PostMetric, PostStatus, SessionLocal

log = logging.getLogger("autopost")

W_INSTALL, W_SAVE, W_ENGAGE = 0.5, 0.3, 0.2


def _latest_metric(post: Post) -> Optional[PostMetric]:
    if not post.metrics:
        return None
    return sorted(post.metrics, key=lambda m: m.captured_at or 0)[-1]


def _measured_posts(session) -> list[Post]:
    return [
        p for p in session.query(Post).filter(Post.status == PostStatus.PUBLISHED).all()
        if p.metrics
    ]


def _installs_map(session) -> dict[str, int]:
    rows = session.execute(
        select(Install.utm_content, func.coalesce(func.sum(Install.count), 0)).group_by(
            Install.utm_content
        )
    ).all()
    return {r[0]: int(r[1]) for r in rows}


def _minmax(values: list[float]) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi == lo:
        # No variance (e.g. a single measured post): treat all as known-good so the
        # bandit prefers measured combos over unscored (default 0.0) ones.
        return {i: 1.0 for i in range(len(values))}
    span = hi - lo
    return {i: (v - lo) / span for i, v in enumerate(values)}


def scored_posts() -> list[dict]:
    """Return each measured post with a normalized performance_score in [0,1]."""
    with SessionLocal() as s:
        posts = _measured_posts(s)
        installs = _installs_map(s)
        rows = []
        for p in posts:
            m = _latest_metric(p)
            reach = max(m.reach or 0, 1)
            inst = installs.get(p.utm_content or "", 0)
            rows.append({
                "post": p,
                "install_rate": inst / reach,
                "save_rate": (m.saved or 0) / reach,
                "eng_rate": (m.total_interactions or 0) / reach,
                "installs": inst,
            })

    if not rows:
        return []

    n_inst = _minmax([r["install_rate"] for r in rows])
    n_save = _minmax([r["save_rate"] for r in rows])
    n_eng = _minmax([r["eng_rate"] for r in rows])
    out = []
    for i, r in enumerate(rows):
        score = W_INSTALL * n_inst[i] + W_SAVE * n_save[i] + W_ENGAGE * n_eng[i]
        p = r["post"]
        out.append({
            "post_id": p.id, "pillar": p.pillar, "feature": p.feature_id,
            "angle": p.angle_id, "format": p.format.value if p.format else None,
            "traits": p.traits or {}, "installs": r["installs"],
            "score": round(score, 4),
        })
    return sorted(out, key=lambda x: x["score"], reverse=True)


def combo_scores() -> dict[tuple[str, str], float]:
    """Average score per (feature, angle) — drives the bandit picker. Feature pillar only."""
    agg: dict[tuple[str, str], list[float]] = {}
    for r in scored_posts():
        if r["pillar"] != "feature":
            continue
        agg.setdefault((r["feature"], r["angle"]), []).append(r["score"])
    return {k: sum(v) / len(v) for k, v in agg.items()}


def winning_patterns() -> dict:
    """The DNA of the top tier of posts, for prompt injection."""
    ranked = scored_posts()
    if not ranked:
        return {}
    top = ranked[: max(1, len(ranked) // 3)]

    def common(key, from_traits=False, limit=3):
        c = Counter()
        for r in top:
            val = (r["traits"].get(key) if from_traits else r.get(key))
            if isinstance(val, list):
                c.update(val)
            elif val:
                c[val] += 1
        return [k for k, _ in c.most_common(limit)]

    return {
        "pillars": common("pillar"),
        "formats": common("format"),
        "hook_styles": common("hook_style", from_traits=True),
        "cta_styles": common("cta_style", from_traits=True),
        "keywords": common("keywords", from_traits=True, limit=5),
        "sample_size": len(ranked),
    }


def compute_weekly_learnings(week_of: str) -> dict:
    patterns = winning_patterns()
    if not patterns:
        return {"status": "no_data"}
    ranked = scored_posts()
    top_score = ranked[0]["score"] if ranked else None
    with SessionLocal() as s:
        s.add(Learning(week_of=week_of, pattern=patterns, performance_score=top_score))
        s.commit()
    log.info("weekly learnings stored for %s: %s", week_of, patterns)
    return {"status": "stored", "patterns": patterns}
