"""Central configuration. Every secret/tunable is read from the environment so
nothing sensitive is ever committed and the Gemini model id (C4) is a one-line swap.

Uses pydantic-settings: values come from environment variables or a local .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    app_name: str = "FieldCalc"
    play_store_url: str = (
        "https://play.google.com/store/apps/details?"
        "id=com.voice.gps.navigation.map.location.route"
    )
    timezone: str = "Asia/Kolkata"  # pin TZ; store UTC, display local
    post_time_local: str = "09:00"  # daily generation time

    # --- Database (C1: durable state lives in Postgres, NOT ephemeral disk) ---
    # e.g. postgresql+psycopg://user:pass@host/db  (Neon connection string)
    database_url: str = "sqlite:///./local_dev.db"  # safe local default

    # --- Caption LLM (C4: model id is env-driven so deprecations are one line) ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_daily_cap: int = 10  # hard ceiling on Gemini calls per day
    # Fallback LLM so a single deprecation never halts a post.
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Instagram / Meta Graph API ---
    ig_user_id: str = ""
    ig_access_token: str = ""  # long-lived; auto-refreshed ~day 45-50
    meta_app_id: str = ""
    meta_app_secret: str = ""
    ig_max_posts_per_day: int = 45  # stay under Meta's 50/24h publish cap
    # Which Graph host to call. Instagram Business Login tokens (IGAA...) use
    # graph.instagram.com; classic Facebook-Page tokens use graph.facebook.com.
    graph_base_url: str = "https://graph.instagram.com/v21.0"
    # Set false ONLY to test from behind a TLS-intercepting proxy (e.g. office
    # WiFi that filters social media). Never ship this false to production.
    ssl_verify: bool = True

    # --- Image generation ---
    image_gen_provider: str = "pollinations"  # "pollinations" (free) or "openai"
    openai_api_key: str = ""  # for DALL-E 3

    # --- Image hosting (Graph API needs a public image URL) ---
    cloudinary_url: str = ""  # fallback host
    public_asset_base_url: str = ""  # e.g. GitHub Pages base, primary host

    # --- Notifications & ops ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_email: str = ""
    smtp_password: str = ""  # Gmail App Password, not the real password
    notification_email: str = ""
    healthcheck_ping_url: str = ""  # Healthchecks.io dead-man's-switch
    sentry_dsn: str = ""

    # --- Security ---
    dashboard_password: str = "change-me"
    run_token: str = "change-me"  # protects the /run-daily cron endpoint

    # --- Behavior flags ---
    dry_run: bool = True  # when True, never actually publishes to Instagram
    imaging_force_pillow: bool = False  # tests/CI set this to skip the Chromium dependency
    formats_enabled: List[str] = Field(default_factory=lambda: ["SINGLE", "STORY"])

    # Learning loop (Sprint 6)
    bandit_epsilon: float = 0.2   # explore probability once performance data exists
    bandit_cooldown: int = 10     # don't re-pick a winning combo within N recent posts

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgres")


@lru_cache
def get_settings() -> Settings:
    return Settings()
