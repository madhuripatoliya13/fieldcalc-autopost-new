"""Shared resilience helpers. Every external call (Meta, LLMs, image hosts) wraps
in this so a transient blip retries with backoff + jitter instead of failing the
day's post. Respects HTTP 429 where the caller surfaces it.
"""
from __future__ import annotations

import logging

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger("autopost")


class TransientError(Exception):
    """Raise for errors worth retrying (network, 5xx, 429)."""


class PermanentError(Exception):
    """Raise for errors that should NOT retry (bad request, auth, policy)."""


def resilient(attempts: int = 4):
    """Decorator: retry on TransientError with exponential backoff + jitter."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=1, max=30),
        retry=retry_if_exception_type(TransientError),
        before_sleep=lambda rs: log.warning(
            "retry %s/%s after %s", rs.attempt_number, attempts, rs.outcome.exception()
        ),
    )
