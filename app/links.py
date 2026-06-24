"""UTM-tagged Play Store links for install attribution (C8).

The Play Store passes a `referrer` value straight through to the installed app via
the Play Install Referrer API. We encode our per-post utm_content there so the
Android app (Sprint 5 client code) can read it on first launch and report which
post drove the install.

referrer scheme:  utm_source=instagram&utm_medium=social&utm_campaign={pillar}&utm_content={feature}-{angle}-{postid}
"""
from __future__ import annotations

from urllib.parse import quote

from app.config import get_settings

settings = get_settings()


def referrer_string(utm_content: str, pillar: str = "feature", source: str = "instagram") -> str:
    return (
        f"utm_source={source}&utm_medium=social"
        f"&utm_campaign={pillar}&utm_content={utm_content}"
    )


def play_referrer_url(utm_content: str, pillar: str = "feature") -> str:
    """Full Play Store URL with the encoded referrer appended."""
    base = settings.play_store_url
    sep = "&" if "?" in base else "?"
    inner = referrer_string(utm_content, pillar)
    return f"{base}{sep}referrer={quote(inner, safe='')}"


def short_link(utm_content: str) -> str:
    """If a Cloudflare Workers shortener is configured (PUBLIC_ASSET_BASE_URL acting
    as the short domain), return a first-party short link that redirects to the
    Play referrer URL and logs the click. Otherwise return the full referrer URL."""
    if settings.public_asset_base_url:
        base = settings.public_asset_base_url.rstrip("/")
        return f"{base}/go/{utm_content}"
    return play_referrer_url(utm_content)
