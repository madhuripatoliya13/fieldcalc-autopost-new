"""DB-agnostic backup: export all tables to a timestamped JSON file (protects the
rotation history + learnings if the DB is ever lost). No pg_dump dependency, so it
works the same on SQLite locally and Postgres in production.

Optional: upload the file to Backblaze B2 (10GB free) — left as a documented step.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.database import AppToken, Install, Learning, Post, PostMetric, SessionLocal

log = logging.getLogger("autopost")

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

_TABLES = [Post, PostMetric, Install, Learning, AppToken]


def _row_to_dict(row) -> dict:
    out = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif hasattr(val, "value"):  # Enum
            val = val.value
        out[col.name] = val
    return out


def export_json() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data: dict = {}
    with SessionLocal() as s:
        for model in _TABLES:
            # Never back up secrets in plaintext.
            if model is AppToken:
                data[model.__tablename__] = [
                    {"name": r.name, "expires_at": r.expires_at.isoformat() if r.expires_at else None}
                    for r in s.query(model).all()
                ]
            else:
                data[model.__tablename__] = [_row_to_dict(r) for r in s.query(model).all()]
    path = BACKUP_DIR / f"backup-{stamp}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    log.info("backup written: %s", path)
    return str(path)
