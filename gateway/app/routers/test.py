"""Live end-to-end test route for Paradox-DB Gateway.

Hit GET /test to run a full integration test suite against the live
Docker environment (PostgreSQL, Redis, Telegram).

Each step is reported independently so partial failures are visible.
"""

import base64
import hashlib
import time
import traceback
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory, get_db
from app.models import DatabaseVersion, UserChannel, VersionHistory
from app.services.telegram import TelegramClient

router = APIRouter()

_TEST_USER_ID = "u_e2e_test"
_TEST_DB_NAME = "e2e_test.db"
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
            required = {"database_versions", "sync_log", "user_channels", "version_history"}
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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

    # ── 8. Registry: Write & Read ────────────────────────────────────
    s = _step("registry_write_read")
    t0 = time.time()
    try:
        async with async_session_factory() as session:
            existing = await session.execute(
                text(
                    "SELECT user_id FROM user_channels WHERE user_id = :uid"
                ),
                {"uid": _TEST_USER_ID},
            )
            if existing.fetchone():
                await session.execute(
                    text("DELETE FROM version_history WHERE user_id = :uid"),
                    {"uid": _TEST_USER_ID},
                )
                await session.execute(
                    text("DELETE FROM database_versions WHERE user_id = :uid"),
                    {"uid": _TEST_USER_ID},
                )
                await session.execute(
                    text("DELETE FROM sync_log WHERE user_id = :uid"),
                    {"uid": _TEST_USER_ID},
                )
                await session.execute(
                    text("DELETE FROM user_channels WHERE user_id = :uid"),
                    {"uid": _TEST_USER_ID},
                )
                await session.flush()

            now = datetime.now(timezone.utc)
            now = datetime.utcnow()
            await session.execute(
                text(
                    "INSERT INTO user_channels (user_id, channel_id, bot_token_id, api_key_hash, created_at) "
                    "VALUES (:uid, :cid, :bt, :akh, :ca)"
                ),
                {
                    "uid": _TEST_USER_ID,
                    "cid": _TEST_CHANNEL_ID,
                    "bt": settings.telegram_bot_token,
                    "akh": "e2e_test_hash",
                    "ca": now,
                },
            )
            await session.flush()

            if uploaded_message_id:
                file_hash = hashlib.sha256(_TEST_FILE_CONTENT).hexdigest()
                now = datetime.utcnow()
                await session.execute(
                    text(
                        "INSERT INTO database_versions "
                        "(user_id, database_name, latest_message_id, latest_version, file_hash, uploaded_at) "
                        "VALUES (:uid, :dbn, :mid, :ver, :fh, :ua)"
                    ),
                    {
                        "uid": _TEST_USER_ID,
                        "dbn": _TEST_DB_NAME,
                        "mid": uploaded_message_id,
                        "ver": 1,
                        "fh": file_hash,
                        "ua": now,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO version_history "
                        "(user_id, database_name, version, message_id, file_hash, file_size, version_type, uploaded_at) "
                        "VALUES (:uid, :dbn, :ver, :mid, :fh, :fs, :vt, :ua)"
                    ),
                    {
                        "uid": _TEST_USER_ID,
                        "dbn": _TEST_DB_NAME,
                        "ver": 1,
                        "mid": uploaded_message_id,
                        "fh": file_hash,
                        "fs": len(_TEST_FILE_CONTENT),
                        "vt": "full",
                        "ua": now,
                    },
                )
                await session.commit()

            verify = await session.execute(
                text(
                    "SELECT latest_version, latest_message_id FROM database_versions "
                    "WHERE user_id = :uid AND database_name = :dbn"
                ),
                {"uid": _TEST_USER_ID, "dbn": _TEST_DB_NAME},
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
            for tbl in ["version_history", "database_versions", "sync_log"]:
                await session.execute(
                    text(f"DELETE FROM {tbl} WHERE user_id = :uid"),
                    {"uid": _TEST_USER_ID},
                )
            await session.execute(
                text("DELETE FROM user_channels WHERE user_id = :uid"),
                {"uid": _TEST_USER_ID},
            )
            await session.commit()
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
