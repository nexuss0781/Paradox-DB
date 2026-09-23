#!/usr/bin/env python3
"""Fail-fast container health gate with actionable diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.getenv("HEALTHCHECK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUIRE_TELEGRAM = bool(os.getenv("TELEGRAM_BOT_TOKEN")) and os.getenv(
    "REQUIRE_TELEGRAM_HEALTH", "1"
).lower() not in {"0", "false", "no"}


def request_json(path: str, timeout: float = 5.0) -> tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body[:500]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        return exc.code, body
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def check(path: str, accepted: set[int], label: str) -> bool:
    status, body = request_json(path)
    if status in accepted:
        print(f"[health] PASS {label}: HTTP {status} {json.dumps(body, separators=(',', ':'))}")
        return True
    print(f"[health] FAIL {label}: HTTP {status}; response={json.dumps(body, default=str)}", file=sys.stderr)
    return False


def main() -> int:
    print(f"[health] Checking application at {BASE_URL}")
    checks = [
        ("/dbg/ping", {200}, "diagnostic ping"),
        ("/health", {200}, "liveness"),
        ("/health/ready", {200}, "readiness (PostgreSQL + Redis)"),
    ]
    if REQUIRE_TELEGRAM:
        checks.append(("/health/telegram", {200}, "Telegram"))
    else:
        print("[health] SKIP Telegram: TELEGRAM_BOT_TOKEN is not configured or health is optional")

    results = [check(*item) for item in checks]
    passed = all(results)
    if passed:
        print("[health] PASS all required checks")
        return 0
    print("[health] FAIL container health gate; see the failed check above", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
