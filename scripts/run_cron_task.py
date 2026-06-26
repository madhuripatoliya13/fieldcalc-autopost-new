#!/usr/bin/env python3
"""Call the FieldCalc automation endpoints from cron.

Usage:
  RUN_TOKEN=your-secret ./scripts/run_cron_task.py daily
  RUN_TOKEN=your-secret ./scripts/run_cron_task.py poller

Optional:
  APP_BASE_URL=http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


TASK_ENDPOINTS = {
    "daily": ("POST", "/cron/run-daily"),
    "daily-sync": ("POST", "/run-daily"),
    "poller": ("POST", "/run-poller"),
    "preflight": ("GET", "/preflight"),
    "backup": ("POST", "/run-backup"),
    "insights": ("POST", "/run-insights"),
}


def _fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    task = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    if task not in TASK_ENDPOINTS:
        allowed = ", ".join(sorted(TASK_ENDPOINTS))
        _fail(f"Usage: {sys.argv[0]} <{allowed}>")

    token = os.environ.get("RUN_TOKEN", "").strip()
    if not token:
        _fail("RUN_TOKEN is missing. Put it in your cron command or environment.")

    base_url = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    method, path = TASK_ENDPOINTS[task]
    url = f"{base_url}{path}"
    request = urllib.request.Request(url, method=method, headers={"X-Run-Token": token})

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        _fail(f"{task} failed with HTTP {exc.code}: {body}", code=exc.code)
    except Exception as exc:  # noqa: BLE001 - cron needs a simple, visible error.
        _fail(f"{task} failed: {exc}")

    try:
        parsed = json.loads(body)
        body = json.dumps(parsed, indent=2, sort_keys=True)
    except json.JSONDecodeError:
        pass

    print(f"OK: {task} -> HTTP {status}")
    print(body)


if __name__ == "__main__":
    main()
