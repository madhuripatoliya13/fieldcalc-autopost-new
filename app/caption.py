"""Caption engine: brand-voiced, multi-variant, judge-selected.

generate() produces N variants, scores them (LLM judge when a key is present, else a
local heuristic), and returns the winner assembled into a final caption + metadata.
The same code path works with or without API keys.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app import llm

log = logging.getLogger("autopost")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_BRAND_VOICE = (DATA_DIR / "BRAND_VOICE.md").read_text(encoding="utf-8")

N_VARIANTS = 3


def _winners_hint(winners: dict | None) -> str:
    if not winners:
        return ""
    hooks = ", ".join(winners.get("hook_styles", [])) or "n/a"
    kws = ", ".join(winners.get("keywords", [])) or "n/a"
    return (
        "\nPROVEN WINNERS (emulate what's working): "
        f"top hook styles = [{hooks}]; high-performing keywords = [{kws}]. "
        "Lean toward these without copying past posts.\n"
    )


def _prompt(feature: dict, angle: dict, variant: int, winners: dict | None = None) -> str:
    ctx = {
        "feature": {
            "name": feature["name"],
            "short": feature.get("short", ""),
            "primary_benefit": feature.get("primary_benefit", ""),
            "keywords": feature.get("keywords", []),
            "use_cases": feature.get("use_cases", []),
        },
        "angle": {"name": angle["name"], "cta_style": angle.get("cta_style", "")},
        "variant": variant,
    }
    return f"""{_BRAND_VOICE}

Write an Instagram caption for the app feature below, using the "{angle['name']}" angle.
Guidance: {angle.get('prompt_guidance', '')}
Hook style: {angle.get('hook_style', '')}
{_winners_hint(winners)}

Return a JSON object with keys:
  hook (string, scroll-stopping first line, no emoji, weave in 1-2 keywords),
  body (2-3 short lines, may use 2-4 emoji),
  cta (one line, prefer a save/send action),
  hashtags (array of EXACTLY 4-5 strings, each starting with #, niche + branded),
  alt_text (string, describes the image for accessibility/SEO),
  keywords (array of 2 search keywords used in the hook).

Only describe what this feature actually does. No absolute/accuracy/superlative claims.

CONTEXT_JSON: {json.dumps(ctx)}"""


def _assemble(v: dict) -> str:
    parts = [v.get("hook", "").strip(), v.get("body", "").strip(), v.get("cta", "").strip()]
    return "\n\n".join(p for p in parts if p)


def _judge(feature: dict, angle: dict, variants: list[dict]) -> int:
    """Return the index of the best variant."""
    if llm.provider() != "local":
        try:
            options = "\n".join(f"[{i}] {_assemble(v)}" for i, v in enumerate(variants))
            res = llm.generate_json(
                f"You are an Instagram copy editor. Pick the single best caption for the "
                f"'{angle['name']}' angle promoting {feature['name']}. Judge on hook strength, "
                f"clarity, and a save/send-worthy CTA.\n\n{options}\n\n"
                f'Return JSON: {{"best_index": <int>, "reason": "<short>"}}'
            )
            idx = int(res.get("best_index", 0))
            if 0 <= idx < len(variants):
                return idx
        except Exception as e:  # noqa: BLE001
            log.warning("judge failed (%s); using heuristic", e)
    # Local heuristic: reward a present hook, a save/send CTA, and <=5 hashtags.
    def score(v: dict) -> float:
        s = 0.0
        if v.get("hook"):
            s += 1
        if any(w in v.get("cta", "").lower() for w in ("save", "send", "tag")):
            s += 1
        tags = v.get("hashtags") or []
        if 1 <= len(tags) <= 5:
            s += 1
        s += min(len(v.get("body", "")), 200) / 200.0
        return s

    return max(range(len(variants)), key=lambda i: score(variants[i]))


def generate(feature: dict, angle: dict, *, regen_seed: int = 0, winners: dict | None = None) -> dict:
    """Generate variants, judge, and return the winning caption + metadata."""
    variants = []
    for i in range(N_VARIANTS):
        v = llm.generate_json(_prompt(feature, angle, variant=i + regen_seed, winners=winners))
        # normalize hashtags to <=5
        tags = [t if t.startswith("#") else f"#{t}" for t in (v.get("hashtags") or [])][:5]
        v["hashtags"] = tags
        variants.append(v)

    best_idx = _judge(feature, angle, variants)
    best = variants[best_idx]
    return {
        "caption": _assemble(best),
        "hashtags": best.get("hashtags", []),
        "alt_text": best.get("alt_text", ""),
        "keywords": best.get("keywords", []),
        "variant_index": best_idx,
        "variant_count": len(variants),
        "provider": llm.provider(),
    }
