"""Notifications. Telegram is primary (no SMTP/2FA fragility); Gmail is secondary.
All sends are best-effort and never raise — a notification failure must not break
the pipeline. No-ops cleanly when creds are absent (local dev / DRY_RUN)."""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from app.config import get_settings

log = logging.getLogger("autopost")
settings = get_settings()


def send(subject: str, body: str) -> None:
    _telegram(f"*{subject}*\n{body}")
    _email(subject, body)


def _telegram(text: str) -> None:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        log.info("[notify:telegram skipped] %s", text.replace("\n", " | "))
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            data={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001 - best effort
        log.warning("telegram notify failed: %s", e)


def _email(subject: str, body: str) -> None:
    if not (settings.smtp_email and settings.smtp_password and settings.notification_email):
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.smtp_email
        msg["To"] = settings.notification_email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(settings.smtp_email, settings.smtp_password)
            srv.send_message(msg)
    except Exception as e:  # noqa: BLE001 - best effort
        log.warning("email notify failed: %s", e)
