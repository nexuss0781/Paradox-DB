"""Server-side SQL execution + persistent session engine API.

The persistent session engine lets thin clients (JS SDK, Python SDK,
SQLAlchemy engines) run SQL against a versioned, encrypted SQLite database
that lives on the gateway.  Sessions span requests so ``BEGIN ... COMMIT``
work exactly like a local connection, and every committed change is stored
as a brand new version in Telegram.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Callable, Awaitable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import settings
from ..crypto import encrypt_data
from ..database import async_session_factory, get_db
from ..models import DatabaseVersion, ParadoxDB, SyncLog, User
from ..routers.databases import RedisLock
from ..services.session_engine import (
    SqlExecutionError,
    SqlSession,
    SqlSessionStore,
    is_commit,
)
from ..services.telegram import TelegramClient, TelegramError
from ..telegram_logger import log_operation

router = APIRouter(prefix="/v1", tags=["sql"])

session_store = SqlSessionStore()


class SqlRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="SQL statement to run")
    params: list[Any] = Field(default_factory=list, description="Bind parameters")
    passphrase: str = Field(default="", description="Decryption passphrase (encrypted DBs)")
    executescript: bool = Field(default=False, description="Run as a SQL script")
    flush: bool = Field(default=False, description="Persist the snapshot now")


class SessionCloseRequest(BaseModel):
    commit: bool = Field(default=True, description="Persist dirty work before closing")


# ── helpers ──────────────────────────────────────────────────────


def _make_persister(user_id, database_id) -> Callable[[SqlSession, bytes], Awaitable[int]]:
    async def persister(session: SqlSession, raw: bytes) -> int:
        return await persist_snapshot(user_id, database_id, session, raw)

    return persister


async def _load_snapshot(paradox_db: ParadoxDB) -> tuple[bytes, int]:
    """Download the latest stored snapshot from Telegram (empty for fresh DBs)."""
    if not paradox_db.latest_message_id and not paradox_db.latest_file_id:
        return b"", paradox_db.latest_version
    tg = TelegramClient(
        bot_token=settings.telegram_bot_token,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    try:
        file_bytes = await tg.download_best(
            channel_id=settings.telegram_storage_chat_id,
            message_id=paradox_db.latest_message_id,
            file_id=paradox_db.latest_file_id or "",
        )
    except TelegramError as e:
        raise HTTPException(status_code=502, detail=f"Telegram download failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Snapshot download failed: {e}")
    return file_bytes, paradox_db.latest_version


async def persist_snapshot(
    user_id, database_id: str, session: SqlSession, raw: bytes
) -> int:
    """Push the session's SQLite bytes to Telegram as a new version.

    Re-encrypts to match the stored format (plaintext stays plaintext;
    encrypted snapshots are AES-256-CBC wrapped with the session passphrase).
    """
    payload = raw
    if session.mode == "encrypted":
        payload = encrypt_data(raw, session.passphrase)

    lock = RedisLock()
    lock_key = f"{user_id}:{database_id}"
    try:
        acquired = await lock.acquire(lock_key, timeout=settings.lock_timeout_seconds)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"lock_error: {e}")
    if not acquired:
        raise HTTPException(status_code=503, detail="lock_timeout")

    async with async_session_factory() as db:
        try:
            result = await db.execute(
                select(ParadoxDB).where(
                    ParadoxDB.id == database_id, ParadoxDB.user_id == user_id
                )
            )
            paradox_db = result.scalar_one_or_none()
            if not paradox_db:
                raise HTTPException(status_code=404, detail="Database not found")

            new_version = paradox_db.latest_version + 1
            file_hash = hashlib.sha256(payload).hexdigest()
            caption = {
                "db_name": paradox_db.name,
                "version": new_version,
                "type": "sql",
                "timestamp": datetime.utcnow().isoformat(),
                "hash": file_hash,
                "user_id": str(user_id),
            }

            tg = TelegramClient(
                bot_token=settings.telegram_bot_token,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
            )
            try:
                msg_id, file_id = await tg.upload_file_with_file_id(
                    settings.telegram_storage_chat_id, payload, caption
                )
            except TelegramError as e:
                raise HTTPException(status_code=502, detail=f"Telegram upload failed: {e}")

            paradox_db.latest_version = new_version
            paradox_db.latest_message_id = msg_id
            paradox_db.latest_file_id = file_id or None
            paradox_db.file_hash = file_hash
            paradox_db.updated_at = datetime.utcnow()

            db.add(
                DatabaseVersion(
                    id=uuid.uuid4(),
                    db_id=paradox_db.id,
                    version_number=new_version,
                    file_hash=file_hash,
                    file_size=len(payload),
                    message_id=msg_id,
                    file_id=file_id or None,
                    notes="Committed by server-side SQL session",
                    created_by=user_id,
                    created_at=datetime.utcnow(),
                )
            )
            db.add(
                SyncLog(
                    request_id=str(uuid.uuid4()),
                    user_id=user_id,
                    database_name=paradox_db.name,
                    operation="sql_commit",
                    telegram_message_id=msg_id,
                    status="success",
                    completed_at=datetime.utcnow(),
                )
            )
            await db.commit()
            try:
                await log_operation(
                    "sql_commit",
                    f"{paradox_db.name} v{new_version} committed by SQL session",
                    "success",
                )
            except Exception:
                pass
            return new_version
        except Exception:
            await db.rollback()
            raise
        finally:
            try:
                await lock.release(lock_key)
            except Exception:
                pass


def _session_info(session: SqlSession) -> dict:
    return {
        "id": session.key,
        "mode": session.mode,
        "version": session.base_version,
        "dirty": session.dirty,
        "created_at": session.created_at,
        "idle_seconds": round(session.idle_seconds, 1),
    }


async def _get_or_warm(
    database_id: str, user: User, passphrase: str, db: AsyncSession
):
    paradox_db = await _owned_database(database_id, user, db)
    key = f"{user.id}:{database_id}"
    session = session_store.get(key)
    if session is not None and session.idle_seconds <= session_store._ttl:
        if passphrase and passphrase != session.passphrase:
            await session_store.evict(key)
            session = None
    if session is None:
        snapshot, version = await _load_snapshot(paradox_db)
        try:
            session, _ = await session_store.get_or_create(
                key, passphrase, snapshot, version
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if session_store.get_persister(key) is None:
            session_store.bind_persister(key, _make_persister(user.id, database_id))
    return session, paradox_db


async def _owned_database(database_id: str, user: User, db: AsyncSession):
    result = await db.execute(
        select(ParadoxDB).where(
            ParadoxDB.id == database_id, ParadoxDB.user_id == user.id
        )
    )
    return result.scalar_one_or_none()


# ── endpoints ────────────────────────────────────────────────────


@router.post("/databases/{database_id}/sql")
async def execute_sql(
    database_id: str,
    body: SqlRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a SQL statement in the database's persistent session."""
    session, paradox_db = await _get_or_warm(database_id, user, body.passphrase, db)

    try:
        result = await session_store.execute(
            session, body.sql, body.params, body.executescript
        )
    except SqlExecutionError as e:
        return JSONResponse(
            status_code=400, content={"error": "sql_error", "detail": e.message}
        )

    persisted_version = None
    if body.flush or (not body.executescript and is_commit(body.sql)):
        if session.dirty:
            persister = session_store.get_persister(f"{user.id}:{database_id}")
            if persister is not None:
                persisted_version = await session_store.persist(session, persister)

    data = result.to_dict(session)
    data["persisted_version"] = persisted_version
    return data


@router.post("/databases/{database_id}/sql/session")
async def create_session(
    database_id: str,
    body: SqlRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Warm up the persistent session (loads the latest snapshot)."""
    session, _ = await _get_or_warm(database_id, user, body.passphrase, db)
    return _session_info(session)


@router.get("/databases/{database_id}/sql/session")
async def get_session(
    database_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the persistent session status for a database."""
    await _owned_database(database_id, user, db)
    session = session_store.get(f"{user.id}:{database_id}")
    if session is None:
        return {"session": None}
    return {"session": _session_info(session)}


@router.delete("/databases/{database_id}/sql/session")
async def close_session(
    database_id: str,
    body: SessionCloseRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Close the persistent session, persisting any committed work."""
    await _owned_database(database_id, user, db)
    commit = body.commit if body is not None else True
    key = f"{user.id}:{database_id}"
    session = session_store.get(key)
    if session is None:
        return {"closed": True, "version": None}
    version = None
    if commit and session.dirty:
        persister = session_store.get_persister(key)
        if persister is not None:
            version = await session_store.persist(session, persister)
    await session_store.evict(key)
    return {"closed": True, "version": version}
