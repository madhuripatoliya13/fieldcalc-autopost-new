"""LLM provider chain behind one interface (C4: never single-vendor).

Priority: Gemini -> Groq -> deterministic local generator.
The local generator means the WHOLE system runs and tests with ZERO API keys —
critical for free local development and CI. When a key is present, real models are
used; the interface and JSON contract are identical either way.

A hard daily cap protects the Gemini free tier.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from app.config import get_settings

log = logging.getLogger("autopost")
settings = get_settings()

_call_log = {"date": date.today().isoformat(), "gemini": 0}


def _bump_gemini() -> None:
    today = date.today().isoformat()
    if _call_log["date"] != today:
        _call_log.update(date=today, gemini=0)
    _call_log["gemini"] += 1


def provider() -> str:
    if settings.gemini_api_key:
        return "gemini"
    if settings.groq_api_key:
        return "groq"
    return "local"


def generate_json(prompt: str, *, expect_keys: list[str] | None = None) -> dict:
    """Ask the active provider for a JSON object. Falls back down the chain on error.
    `expect_keys` is only used by the local generator's caller-side validation."""
    p = provider()
    try:
        if p == "gemini":
            return _gemini_json(prompt)
        if p == "groq":
            return _groq_json(prompt)
    except Exception as e:  # noqa: BLE001 — degrade, never crash the daily post
        log.warning("LLM provider %s failed (%s); falling back to local", p, e)
    return _local_json(prompt)


# ---- Gemini ---------------------------------------------------------------
def _gemini_json(prompt: str) -> dict:
    if _call_log["gemini"] >= settings.gemini_daily_cap:
        raise RuntimeError("gemini daily cap reached")
    import google.generativeai as genai  # lazy import

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    _bump_gemini()
    resp = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json", "temperature": 0.9},
    )
    return json.loads(resp.text)


# ---- Groq -----------------------------------------------------------------
def _groq_json(prompt: str) -> dict:
    from groq import Groq  # lazy import

    client = Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON."}],
        response_format={"type": "json_object"},
        temperature=0.9,
    )
    return json.loads(resp.choices[0].message.content)


# ---- Local deterministic fallback ----------------------------------------
def _local_json(prompt: str) -> dict:
    """The caption module passes structured context in the prompt as a JSON tail
    so the local generator can build a sensible response without a model.
    Contract: prompt ends with a line `CONTEXT_JSON: {...}`."""
    ctx = {}
    for line in reversed(prompt.splitlines()):
        if line.startswith("CONTEXT_JSON:"):
            try:
                ctx = json.loads(line[len("CONTEXT_JSON:"):].strip())
            except Exception:  # noqa: BLE001
                ctx = {}
            break
    return _compose_local_caption(ctx)


def _to_tag(s: str) -> str:
    return "#" + "".join(w.capitalize() for w in s.replace("-", " ").split())


def _compose_local_caption(ctx: dict) -> dict:
    feature = ctx.get("feature", {})
    angle = ctx.get("angle", {})
    variant = ctx.get("variant", 0)
    name = feature.get("name", "This feature")
    benefit = feature.get("primary_benefit", "")
    short = feature.get("short", "")
    kws = feature.get("keywords", [])[:2]
    use_case = (feature.get("use_cases") or ["get the job done"])[0]

    hooks = [
        f"{benefit}",
        f"Stop guessing — {short[0].lower() + short[1:] if short else name.lower()}",
        f"Here's how to {use_case} right from your phone.",
    ]
    hook = hooks[variant % len(hooks)]

    body = f"{short} Perfect when you need to {use_case}. 📍✅"
    cta_bank = [
        "Save this for your next field job.",
        "Send this to someone tired of guessing distances.",
        angle.get("cta_style", "Try it free — link in bio."),
    ]
    cta = cta_bank[variant % len(cta_bank)]

    tags = [_to_tag("FieldCalc")] + [_to_tag(k) for k in kws]
    tags = list(dict.fromkeys(tags))[:5]

    return {
        "hook": hook,
        "body": body,
        "cta": cta,
        "hashtags": tags,
        "alt_text": f"Branded FieldCalc graphic about {name}: {short}",
        "keywords": kws,
    }
