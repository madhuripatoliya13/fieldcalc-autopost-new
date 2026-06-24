"""Verification engine. Runs deterministic, free, offline-first gates over a draft
caption and returns a structured report. The pipeline regenerates (up to 3x) on a
BLOCK and surfaces WARN items to the human gate.

Gates:
  claims    — deterministic regex blocklist for false/absolute/trademark claims
              (a real gate; an LLM grading its own output is not). BLOCK or WARN.
  hashtags  — must be 1..5 (C5). BLOCK if >5.
  readability — textstat Flesch-Kincaid grade must be <= max (offline). WARN.
  grammar   — language_tool_python if available (needs Java); else skipped. WARN.
  dedup     — SHA256 of normalized caption vs prior non-rejected posts. BLOCK.
  ai_label  — always flags that a human must confirm the AI-content label (C6).
"""
from __future__ import annotations

import hashlib
import logging
import re

from sqlalchemy import select

from app.database import Post, PostStatus, SessionLocal

log = logging.getLogger("autopost")

READABILITY_MAX_GRADE = 10.0

# Hard-block: claims that are false, absolute, or policy-risky.
BLOCK_PATTERNS = [
    r"\b100\s*%\s*accurate\b",
    r"\bguarantee(d|s)?\b",
    r"\bnever\s+wrong\b",
    r"\balways\s+accurate\b",
    r"\bperfect\s+accuracy\b",
    r"\b#1\b",
    r"\bbest\s+app\s+(ever|in\s+the\s+world)\b",
    r"\bcures?\b",
]
# Warn: needs a human eye (trademarks, "free" wording, etc.).
WARN_PATTERNS = [
    r"\bgoogle\b",
    r"\bapple\b",
    r"\bofficial\b",
    r"\bendorsed\b",
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def caption_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


def _check_claims(text: str) -> tuple[list[str], list[str]]:
    low = text.lower()
    blocks = [p for p in BLOCK_PATTERNS if re.search(p, low)]
    warns = [p for p in WARN_PATTERNS if re.search(p, low)]
    return blocks, warns


def _check_readability(text: str) -> dict:
    try:
        import textstat  # lazy

        grade = textstat.flesch_kincaid_grade(text)
        return {"grade": round(grade, 1), "ok": grade <= READABILITY_MAX_GRADE}
    except Exception as e:  # noqa: BLE001
        return {"grade": None, "ok": True, "skipped": str(e)}


def _check_grammar(text: str) -> dict:
    try:
        import language_tool_python  # lazy; needs Java + local LT

        tool = language_tool_python.LanguageTool("en-US")
        matches = tool.check(text)
        return {"issues": len(matches), "ok": len(matches) <= 2}
    except Exception as e:  # noqa: BLE001
        return {"issues": None, "ok": True, "skipped": str(e)}


def _check_dedup(text: str) -> dict:
    h = caption_hash(text)
    with SessionLocal() as s:
        dup = s.scalar(
            select(Post).where(Post.caption_hash == h, Post.status != PostStatus.REJECTED)
        )
    return {"hash": h, "ok": dup is None, "duplicate_of": dup.id if dup else None}


def run(caption: str, hashtags: list[str]) -> dict:
    """Run all gates; return {ok, blocking[], warnings[], checks{}}."""
    blocks, warns = _check_claims(caption)
    readability = _check_readability(caption)
    grammar = _check_grammar(caption)
    dedup = _check_dedup(caption)

    blocking: list[str] = []
    warnings: list[str] = []

    if blocks:
        blocking.append(f"claim violation: {blocks}")
    if len(hashtags) > 5:
        blocking.append(f"too many hashtags: {len(hashtags)} (max 5)")
    if not dedup["ok"]:
        blocking.append(f"duplicate caption of post #{dedup['duplicate_of']}")

    if warns:
        warnings.append(f"review wording: {warns}")
    if not readability["ok"]:
        warnings.append(f"readability grade {readability['grade']} > {READABILITY_MAX_GRADE}")
    if not grammar["ok"]:
        warnings.append(f"grammar issues: {grammar['issues']}")
    warnings.append("AI-content label: human must confirm before publishing (C6)")

    return {
        "ok": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "checks": {
            "claims": {"blocks": blocks, "warns": warns},
            "hashtags": len(hashtags),
            "readability": readability,
            "grammar": grammar,
            "dedup": dedup,
        },
    }
