"""Weekly performance digest — emailed/Telegram'd so you see what's working without
opening the dashboard. Pure read over the learning layer."""
from __future__ import annotations

from app import attribution, learning, notify


def build_digest() -> str:
    ranked = learning.scored_posts()
    patterns = learning.winning_patterns()
    installs = attribution.installs_by_post()
    total_installs = sum(r["installs"] for r in installs)

    if not ranked:
        return "No measured posts yet — digest will populate once posts collect metrics."

    lines = [f"📊 Weekly digest — {len(ranked)} measured posts, {total_installs} attributed installs", ""]
    lines.append("🏆 Top performers:")
    for r in ranked[:3]:
        lines.append(f"  • {r['feature']} / {r['angle']} ({r['pillar']}) — score {r['score']}, {r['installs']} installs")
    if len(ranked) > 3:
        lines.append("🔻 Needs work:")
        for r in ranked[-2:]:
            lines.append(f"  • {r['feature']} / {r['angle']} — score {r['score']}")
    lines.append("")
    lines.append("✨ Currently winning patterns:")
    lines.append(f"  hooks: {patterns.get('hook_styles')}")
    lines.append(f"  formats: {patterns.get('formats')}")
    lines.append(f"  keywords: {patterns.get('keywords')}")
    return "\n".join(lines)


def send_weekly_digest(week_of: str) -> dict:
    learning.compute_weekly_learnings(week_of)
    body = build_digest()
    notify.send("📊 FieldCalc IG — weekly digest", body)
    return {"status": "sent", "preview": body[:200]}
