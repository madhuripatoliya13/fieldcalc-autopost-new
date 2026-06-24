"""Install attribution ingestion + joins (C8).

Installs arrive from two free sources, both keyed on utm_content:
  - Google Play Console acquisition reports (CSV export / Reporting API)
  - RevenueCat (already integrated in the app) for installs that convert

installs_by_post() joins those counts back to the exact post via utm_content,
finally answering "which feature + angle drives installs" — the signal that
powers the Sprint 6 learning loop.
"""
from __future__ import annotations

import csv
import logging

from sqlalchemy import func, select

from app.database import Install, Post, SessionLocal

log = logging.getLogger("autopost")


def record_install(utm_content: str, source: str = "play_console", count: int = 1) -> None:
    if not utm_content:
        return
    with SessionLocal() as s:
        s.add(Install(utm_content=utm_content, source=source, count=count))
        s.commit()


def installs_for(utm_content: str) -> int:
    with SessionLocal() as s:
        total = s.scalar(
            select(func.coalesce(func.sum(Install.count), 0)).where(
                Install.utm_content == utm_content
            )
        )
    return int(total or 0)


def installs_by_post() -> list[dict]:
    """Each post with its attributed install count (0 if none yet)."""
    with SessionLocal() as s:
        rows = s.execute(
            select(
                Post.id,
                Post.utm_content,
                Post.pillar,
                Post.feature_id,
                Post.angle_id,
                func.coalesce(func.sum(Install.count), 0),
            )
            .join(Install, Install.utm_content == Post.utm_content, isouter=True)
            .group_by(Post.id)
        ).all()
    return [
        {
            "post_id": r[0], "utm_content": r[1], "pillar": r[2],
            "feature": r[3], "angle": r[4], "installs": int(r[5]),
        }
        for r in rows
    ]


def _find_key(headers: list[str], *needles: str) -> str | None:
    for h in headers:
        low = h.lower()
        if any(n in low for n in needles):
            return h
    return None


def import_play_console_csv(path: str) -> int:
    """Best-effort import of a Play Console acquisition CSV. Tolerant of column
    naming: finds a column holding the utm_content (or a referrer/UTM string) and
    a column holding the install/visitor count. Returns rows imported."""
    imported = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return 0
        uc_key = _find_key(reader.fieldnames, "utm_content", "content", "referrer", "utm")
        cnt_key = _find_key(reader.fieldnames, "install", "visitor", "acquisition", "users")
        for row in reader:
            raw = (row.get(uc_key) or "").strip() if uc_key else ""
            utm_content = _extract_utm_content(raw)
            if not utm_content:
                continue
            try:
                count = int(float(row.get(cnt_key, 0) or 0)) if cnt_key else 1
            except ValueError:
                count = 1
            record_install(utm_content, "play_console", count)
            imported += 1
    log.info("imported %d install rows from %s", imported, path)
    return imported


def _extract_utm_content(raw: str) -> str:
    """Pull utm_content out of either a bare value or a full referrer query string."""
    if "utm_content=" in raw:
        part = raw.split("utm_content=", 1)[1]
        return part.split("&", 1)[0]
    return raw
