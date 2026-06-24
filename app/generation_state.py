"""Small durable cursor for resetting the feature/angle generation plan.

The post table remains the source of history, but this cursor lets the dashboard
start a fresh planning cycle without deleting old approvals/rejections.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _state_path() -> Path:
    settings = get_settings()
    if "autopost_test.db" in settings.database_url:
        return Path(tempfile.gettempdir()) / "autopost_generation_state_test.json"
    return DATA_DIR / "generation_state.json"


def load() -> dict:
    settings = get_settings()
    path = _state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if state.get("database_url") != settings.database_url:
        return {"database_url": settings.database_url, "reset_after_post_id": 0, "reset_at": ""}
    state.setdefault("reset_after_post_id", 0)
    state.setdefault("reset_at", "")
    return state


def reset_after_post_id() -> int:
    return int(load().get("reset_after_post_id") or 0)


def set_reset_after(post_id: int) -> dict:
    settings = get_settings()
    state = {
        "database_url": settings.database_url,
        "reset_after_post_id": int(post_id or 0),
        "reset_at": datetime.now(timezone.utc).isoformat(),
    }
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state
