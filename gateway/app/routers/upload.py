import hashlib
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, rate_limiter
from app.config import settings
from app.database import get_db
from app.metrics import (
    sync_uploads_failed,
    sync_uploads_success,
    sync_uploads_total,
    sync_upload_latency_ms,
)
from app.models import DatabaseVersion, SyncLog, UserChannel, VersionHistory
from app.services.telegram import TelegramClient, TelegramRateLimitError
from app.telegram_logger import log_operation

import time
import os
import httpx

logger = logging.getLogger("gateway.upload")

router = APIRouter()


class RedisLock:
    def __init__(self):
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    async def acquire(self, key: str, timeout: int = 30) -> bool:
        redis = await self._get_redis()
        lock_key = f"lock:upload:{key}"
        return await redis.set(lock_key, "1", nx=True, ex=timeout)

    async def release(self, key: str):
        redis = await self._get_redis()
        lock_key = f"lock:upload:{key}"
        await redis.delete(lock_key)


_upload_lock = RedisLock()


@router.post("/upload")
async def upload(
    request: Request,
    user: UserChannel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sync_uploads_total.inc()
    start = time.time()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    database_name = body.get("database_name", "")
    file_data_b64 = body.get("file_data", "")
    changeset_data_b64 = body.get("changeset_data", "")
    version_type = body.get("version_type", "auto")
    client_version = body.get("version")

    if not database_name:
        try:
            await log_operation("upload", f"Missing database_name from user {user.user_id}", "fail")
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": "missing database_name"})

    if not file_data_b64 and not changeset_data_b64:
        try:
            await log_operation("upload", f"Missing file_data for {database_name}", "fail")
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": "missing file_data or changeset_data"})

    import base64

    if changeset_data_b64:
        try:
            file_bytes = base64.b64decode(changeset_data_b64)
        except Exception:
            try:
                await log_operation("upload", f"Invalid base64 changeset from user {user.user_id}: {database_name}", "fail")
            except Exception:
                pass
            return JSONResponse(status_code=400, content={"error": "invalid base64 in changeset_data"})
    else:
        try:
            file_bytes = base64.b64decode(file_data_b64)
        except Exception:
            try:
                await log_operation("upload", f"Invalid base64 file_data from user {user.user_id}: {database_name}", "fail")
            except Exception:
                pass
            return JSONResponse(status_code=400, content={"error": "invalid base64 in file_data"})

    if not rate_limiter.check(user.user_id):
        try:
            await log_operation("upload", f"Rate limited: user {user.user_id}", "warn")
        except Exception:
            pass
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "retry_after_seconds": 60, "queue_depth": 0},
        )

    lock_key = f"{user.user_id}:{database_name}"
    try:
        acquired = await _upload_lock.acquire(lock_key, timeout=settings.lock_timeout_seconds)
    except Exception as e:
        try:
            await log_operation("upload", f"Lock error: {database_name} — {e}", "fail")
        except Exception:
            pass
        return JSONResponse(
            status_code=503,
            content={"error": "lock_error", "detail": str(e)},
        )
    if not acquired:
        try:
            await log_operation("upload", f"Lock timeout: {database_name}", "warn")
        except Exception:
            pass
        return JSONResponse(
            status_code=503,
            content={"error": "lock_timeout", "detail": "Another upload is in progress"},
        )

    try:
        # Check version conflict
        result = await db.execute(
            select(DatabaseVersion).where(
                DatabaseVersion.user_id == user.user_id,
                DatabaseVersion.database_name == database_name,
            )
        )
        existing = result.scalar_one_or_none()

        if existing and client_version is not None and client_version < existing.latest_version:
            await log_operation(
                "upload",
                f"Conflict: {database_name} remote=v{existing.latest_version} client=v{client_version}",
                "warn",
            )
            return JSONResponse(
                status_code=409,
                content={
                    "error": "conflict_detected",
                    "remote_version": existing.latest_version,
                    "your_version": client_version,
                    "remote_message_id": existing.latest_message_id,
                    "resolution": "pull_before_push",
                },
            )

        new_version = (existing.latest_version + 1) if existing else 1
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        request_id = str(uuid.uuid4())

        # Log pending
        log_entry = SyncLog(
            request_id=request_id,
            user_id=user.user_id,
            database_name=database_name,
            operation="upload",
            status="pending",
        )
        db.add(log_entry)
        await db.flush()

        # Upload to Telegram
        tg_client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )

        caption = {
            "db_name": database_name,
            "version": new_version,
            "type": version_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hash": file_hash,
            "user_id": user.user_id,
        }

        try:
            message_id = await tg_client.upload_file(user.channel_id, file_bytes, caption)
        except TelegramRateLimitError as e:
            sync_uploads_failed.inc()
            log_entry.status = "failed"
            log_entry.error_message = f"Telegram rate limit: retry_after={e.retry_after}"
            log_entry.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await log_operation("upload", f"Telegram rate limit: {database_name}", "fail")
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "retry_after_seconds": e.retry_after, "queue_depth": 1},
            )
        except Exception as e:
            sync_uploads_failed.inc()
            log_entry.status = "failed"
            log_entry.error_message = str(e)
            log_entry.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await log_operation("upload", f"Telegram failed: {database_name} — {e}", "fail")
            return JSONResponse(status_code=502, content={"error": "telegram_upload_failed", "detail": str(e)})

        await log_operation(
            "upload",
            f"File sent to Telegram: {database_name} v{new_version} msg={message_id} {len(file_bytes)}B",
            "success",
        )

        # Update registry
        if existing:
            existing.latest_message_id = message_id
            existing.latest_version = new_version
            existing.file_hash = file_hash
            existing.uploaded_at = datetime.now(timezone.utc)
        else:
            new_entry = DatabaseVersion(
                user_id=user.user_id,
                database_name=database_name,
                latest_message_id=message_id,
                latest_version=new_version,
                file_hash=file_hash,
            )
            db.add(new_entry)

        history_entry = VersionHistory(
            user_id=user.user_id,
            database_name=database_name,
            version=new_version,
            message_id=message_id,
            file_hash=file_hash,
            file_size=len(file_bytes),
            version_type=version_type if version_type != "auto" else "full",
            uploaded_at=datetime.now(timezone.utc),
        )
        db.add(history_entry)

        log_entry.telegram_message_id = message_id
        log_entry.status = "success"
        log_entry.completed_at = datetime.now(timezone.utc)
        await db.commit()

        duration_ms = (time.time() - start) * 1000
        sync_upload_latency_ms.observe(duration_ms)
        sync_uploads_success.inc()

        await log_operation(
            "upload",
            f"{database_name} v{new_version} msg={message_id} user={user.user_id} {len(file_bytes)}B {duration_ms:.0f}ms",
            "success",
        )

        return {
            "request_id": request_id,
            "message_id": message_id,
            "version": new_version,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        sync_uploads_failed.inc()
        await db.rollback()
        await log_operation("upload", f"Internal error: {database_name} user={user.user_id} — {e}", "fail")
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})
    finally:
        try:
            await _upload_lock.release(lock_key)
        except Exception:
            pass
