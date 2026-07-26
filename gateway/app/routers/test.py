"""Live end-to-end test route for Paradox-DB Gateway.

Hit GET /test to run a full integration test suite against the live
Docker environment (PostgreSQL, Redis, Telegram).

Each step is reported independently so partial failures are visible.
"""

import base64
import hashlib
import secrets
import time
import traceback
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory, get_db
from app.services.telegram import TelegramClient
from app.telegram_logger import log_operation

router = APIRouter()

_TEST_USER_ID = f"u_e2e_{secrets.token_hex(8)}"
_TEST_EMAIL = f"e2e_{secrets.token_hex(8)}@test.paradox"
_TEST_USERNAME = f"e2e_{secrets.token_hex(6)}"
_TEST_DB_NAME = f"e2e_test_{secrets.token_hex(4)}.db"
_TEST_FILE_CONTENT = b"paradox-e2e-test-payload-1234567890"
_TEST_CHANNEL_ID = settings.telegram_storage_chat_id


def _step(name: str):
    return {"name": name, "status": "pending", "duration_ms": 0, "error": None}


async def _run_test():
    results = []
    start_all = time.time()

    # ── 1. Health: PostgreSQL ────────────────────────────────────────
    s = _step("postgres_connect")
    t0 = time.time()
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        s["status"] = "pass"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 2. Health: Redis ─────────────────────────────────────────────
    s = _step("redis_connect")
    t0 = time.time()
    try:
        import redis.asyncio as aioredis

        async with aioredis.from_url(settings.redis_url, decode_responses=True) as r:
            await r.ping()
        s["status"] = "pass"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 3. Health: Telegram Bot ──────────────────────────────────────
    s = _step("telegram_bot")
    t0 = time.time()
    try:
        tg = TelegramClient(bot_token=settings.telegram_bot_token)
        healthy = await tg.is_healthy()
        if healthy:
            s["status"] = "pass"
        else:
            s["status"] = "fail"
            s["error"] = "Bot token invalid or Telegram unreachable"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 4. Health: Storage Chat ──────────────────────────────────────
    s = _step("storage_chat")
    t0 = time.time()
    try:
        if not _TEST_CHANNEL_ID:
            s["status"] = "skip"
            s["error"] = "TELEGRAM_STORAGE_CHAT_ID not set"
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getChat",
                    json={"chat_id": _TEST_CHANNEL_ID},
                )
                if resp.status_code == 200 and resp.json().get("ok"):
                    s["status"] = "pass"
                else:
                    s["status"] = "fail"
                    s["error"] = f"getChat returned {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 5. Database: Schema Check ────────────────────────────────────
    s = _step("db_schema")
    t0 = time.time()
    try:
        async with async_session_factory() as session:
            res = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            tables = sorted([row[0] for row in res.fetchall()])
            required = {"users", "projects", "paradox_dbs", "database_versions", "database_backups", "sync_log", "conflict_log"}
            missing = required - set(tables)
            if missing:
                s["status"] = "fail"
                s["error"] = f"Missing tables: {missing}. Found: {tables}"
            else:
                s["status"] = "pass"
                s["tables"] = tables
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 6. Telegram: Upload Test File ────────────────────────────────
    s = _step("telegram_upload")
    uploaded_message_id = None
    uploaded_file_id = None
    t0 = time.time()
    try:
        if not _TEST_CHANNEL_ID:
            s["status"] = "skip"
            s["error"] = "TELEGRAM_STORAGE_CHAT_ID not set"
        else:
            tg = TelegramClient(
                bot_token=settings.telegram_bot_token,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
            )
            caption = {
                "db_name": _TEST_DB_NAME,
                "version": 1,
                "type": "full",
                "timestamp": datetime.utcnow().isoformat(),
                "hash": hashlib.sha256(_TEST_FILE_CONTENT).hexdigest(),
                "user_id": _TEST_USER_ID,
            }
            uploaded_message_id, uploaded_file_id = await tg.upload_file_with_file_id(
                _TEST_CHANNEL_ID, _TEST_FILE_CONTENT, caption
            )
            s["status"] = "pass"
            s["message_id"] = uploaded_message_id
            s["file_id"] = uploaded_file_id
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 7. Telegram: Download Test File ──────────────────────────────
    s = _step("telegram_download")
    t0 = time.time()
    downloaded_bytes = None
    try:
        if not uploaded_file_id or not _TEST_CHANNEL_ID:
            s["status"] = "skip"
            s["error"] = "Upload did not produce a file_id"
        else:
            tg = TelegramClient(
                bot_token=settings.telegram_bot_token,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
            )
            downloaded_bytes = await tg.download_file_by_id(uploaded_file_id)
            if downloaded_bytes == _TEST_FILE_CONTENT:
                s["status"] = "pass"
                s["size_bytes"] = len(downloaded_bytes)
            else:
                s["status"] = "fail"
                s["error"] = (
                    f"Content mismatch: sent {len(_TEST_FILE_CONTENT)} bytes, "
                    f"received {len(downloaded_bytes)} bytes"
                )
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 8. Registry: Write & Read (new schema) ──────────────────────
    s = _step("registry_write_read")
    t0 = time.time()
    try:
        async with async_session_factory() as session:
            now = datetime.utcnow()

            # Create test user
            await session.execute(
                text(
                    "INSERT INTO users (id, email, username, password_hash, is_active, created_at, updated_at) "
                    "VALUES (:id, :email, :username, :pw, true, :ca, :ua)"
                ),
                {"id": _TEST_USER_ID, "email": _TEST_EMAIL, "username": _TEST_USERNAME,
                 "pw": "e2e_test_hash", "ca": now, "ua": now},
            )
            await session.flush()

            # Create test project
            project_id = f"proj_e2e_{secrets.token_hex(8)}"
            await session.execute(
                text(
                    "INSERT INTO projects (id, user_id, name, description, created_at, updated_at) "
                    "VALUES (:id, :uid, :name, :desc, :ca, :ua)"
                ),
                {"id": project_id, "uid": _TEST_USER_ID, "name": "E2E Test Project",
                 "desc": "Automated test", "ca": now, "ua": now},
            )
            await session.flush()

            # Create test database
            db_id = f"db_e2e_{secrets.token_hex(8)}"
            file_hash = hashlib.sha256(_TEST_FILE_CONTENT).hexdigest()
            await session.execute(
                text(
                    "INSERT INTO paradox_dbs (id, project_id, user_id, name, latest_version, latest_message_id, file_hash, created_at, updated_at) "
                    "VALUES (:id, :pid, :uid, :name, :ver, :mid, :fh, :ca, :ua)"
                ),
                {"id": db_id, "pid": project_id, "uid": _TEST_USER_ID,
                 "name": _TEST_DB_NAME, "ver": 1, "mid": uploaded_message_id,
                 "fh": file_hash, "ca": now, "ua": now},
            )
            await session.flush()

            # Create version record
            ver_id = f"ver_e2e_{secrets.token_hex(8)}"
            await session.execute(
                text(
                    "INSERT INTO database_versions (id, db_id, version_number, file_hash, file_size, message_id, created_by, created_at) "
                    "VALUES (:id, :dbid, :ver, :fh, :fs, :mid, :cb, :ca)"
                ),
                {"id": ver_id, "dbid": db_id, "ver": 1, "fh": file_hash,
                 "fs": len(_TEST_FILE_CONTENT), "mid": uploaded_message_id,
                 "cb": _TEST_USER_ID, "ca": now},
            )
            await session.commit()

            # Read back
            verify = await session.execute(
                text(
                    "SELECT pv.latest_version, pv.latest_message_id FROM paradox_dbs pv "
                    "WHERE pv.id = :dbid"
                ),
                {"dbid": db_id},
            )
            row = verify.fetchone()
            if row and row[0] == 1 and row[1] == uploaded_message_id:
                s["status"] = "pass"
            elif row:
                s["status"] = "fail"
                s["error"] = f"Read back wrong data: version={row[0]}, msg_id={row[1]}"
            else:
                s["status"] = "fail"
                s["error"] = "Row not found after insert"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── 9. Cleanup: Remove test data ─────────────────────────────────
    s = _step("cleanup")
    t0 = time.time()
    try:
        async with async_session_factory() as session:
            await session.execute(text("DELETE FROM database_versions WHERE created_by = :uid"), {"uid": _TEST_USER_ID})
            await session.execute(text("DELETE FROM sync_log WHERE user_id = :uid"), {"uid": _TEST_USER_ID})
            await session.execute(text("DELETE FROM conflict_log WHERE user_id = :uid"), {"uid": _TEST_USER_ID})
            await session.execute(text("DELETE FROM database_backups WHERE user_id = :uid"), {"uid": _TEST_USER_ID})
            await session.execute(text("DELETE FROM paradox_dbs WHERE user_id = :uid"), {"uid": _TEST_USER_ID})
            await session.execute(text("DELETE FROM projects WHERE user_id = :uid"), {"uid": _TEST_USER_ID})
            await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": _TEST_USER_ID})
            await session.commit()
        s["status"] = "pass"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # Pre-compute summary for logging
    total_ms = round((time.time() - start_all) * 1000, 1)
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] == "skip")

    # ── 10. Send each log to Telegram channel ────────────────────────
    s = _step("send_to_channel")
    t0 = time.time()
    try:
        # Send header
        await log_operation("e2e_test", f"E2E Test Run — {passed}/{len(results)} passed | {total_ms}ms | {'PASS' if failed == 0 else 'FAIL'}", "success" if failed == 0 else "fail")

        # Send each step
        for r in results:
            icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}.get(r["status"], "?")
            log_detail = f"{r['status'].upper()} — {r['duration_ms']}ms"
            if r["error"]:
                log_detail += f" — {r['error'][:200]}"
            await log_operation("e2e_test", f"{icon} {r['name']}: {log_detail}", "success" if r["status"] == "pass" else "fail" if r["status"] == "fail" else "info")

        s["status"] = "pass"
    except Exception as e:
        s["status"] = "fail"
        s["error"] = str(e)
    s["duration_ms"] = round((time.time() - t0) * 1000, 1)
    results.append(s)

    # ── Summary ──────────────────────────────────────────────────────
    total_ms = round((time.time() - start_all) * 1000, 1)
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] == "skip")

    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total_duration_ms": total_ms,
            "overall": "pass" if failed == 0 else "fail",
        },
        "steps": results,
    }


@router.get("/test")
async def run_e2e_test():
    """Run full end-to-end integration test suite.

    Tests live connectivity to PostgreSQL, Redis, Telegram Bot API,
    storage chat, database schema, upload/download round-trip,
    and registry read/write.

    Returns a JSON report with per-step results.
    """
    try:
        report = await _run_test()
    except Exception as e:
        report = {
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "total_duration_ms": 0,
                "overall": "error",
            },
            "steps": [
                {
                    "name": "test_runner",
                    "status": "fail",
                    "duration_ms": 0,
                    "error": f"{e}\n{traceback.format_exc()}",
                }
            ],
        }
    return report
