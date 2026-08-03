"""Databases CRUD + versions + diff + backup/restore + upload/download."""

import hashlib
import time
import uuid
import base64
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, rate_limiter
from ..config import settings
from ..database import get_db
from ..models import (
    User, Project, ParadoxDB, DatabaseVersion, DatabaseBackup, SyncLog,
    DatabaseCreate, DatabaseUpdate, DatabaseResponse,
    VersionResponse, BackupCreate, BackupResponse, DiffResponse,
)
from ..services.telegram import TelegramClient, TelegramRateLimitError
from ..telegram_logger import log_operation

router = APIRouter(prefix="/v1", tags=["databases"])


# ── Database CRUD ─────────────────────────────────────────────

@router.get("/projects/{project_id}/databases", response_model=list[DatabaseResponse])
async def list_databases(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all databases in a project."""
    proj = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    if not proj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(ParadoxDB).where(
            ParadoxDB.project_id == project_id,
            ParadoxDB.user_id == user.id,
        ).order_by(ParadoxDB.created_at.desc())
    )
    databases = result.scalars().all()
    return [
        DatabaseResponse(
            id=str(d.id), project_id=str(d.project_id), name=d.name,
            description=d.description, latest_version=d.latest_version,
            file_hash=d.file_hash,
            created_at=d.created_at.isoformat() if d.created_at else "",
            updated_at=d.updated_at.isoformat() if d.updated_at else "",
        )
        for d in databases
    ]


@router.post("/projects/{project_id}/databases", response_model=DatabaseResponse, status_code=201)
async def create_database(
    project_id: str,
    body: DatabaseCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new database in a project."""
    proj = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    if not proj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    paradox_db = ParadoxDB(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id) if isinstance(project_id, str) else project_id,
        user_id=user.id,
        name=body.name,
        description=body.description,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(paradox_db)
    await db.flush()

    return DatabaseResponse(
        id=str(paradox_db.id), project_id=str(paradox_db.project_id), name=paradox_db.name,
        description=paradox_db.description, latest_version=0, file_hash=None,
        created_at=paradox_db.created_at.isoformat(),
        updated_at=paradox_db.updated_at.isoformat(),
    )


@router.get("/databases/{database_id}", response_model=DatabaseResponse)
async def get_database(
    database_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get database details."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    return DatabaseResponse(
        id=str(paradox_db.id), project_id=str(paradox_db.project_id), name=paradox_db.name,
        description=paradox_db.description, latest_version=paradox_db.latest_version,
        file_hash=paradox_db.file_hash,
        created_at=paradox_db.created_at.isoformat() if paradox_db.created_at else "",
        updated_at=paradox_db.updated_at.isoformat() if paradox_db.updated_at else "",
    )


@router.put("/databases/{database_id}", response_model=DatabaseResponse)
async def update_database(
    database_id: str,
    body: DatabaseUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update database name/description."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    if body.name is not None:
        paradox_db.name = body.name
    if body.description is not None:
        paradox_db.description = body.description
    paradox_db.updated_at = datetime.utcnow()
    await db.flush()

    return DatabaseResponse(
        id=str(paradox_db.id), project_id=str(paradox_db.project_id), name=paradox_db.name,
        description=paradox_db.description, latest_version=paradox_db.latest_version,
        file_hash=paradox_db.file_hash,
        created_at=paradox_db.created_at.isoformat() if paradox_db.created_at else "",
        updated_at=paradox_db.updated_at.isoformat() if paradox_db.updated_at else "",
    )


@router.delete("/databases/{database_id}")
async def delete_database(
    database_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a database and all its versions/backups."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    # Delete versions and backups
    await db.execute(delete(DatabaseVersion).where(DatabaseVersion.db_id == database_id))
    await db.execute(delete(DatabaseBackup).where(DatabaseBackup.db_id == database_id))
    await db.delete(paradox_db)
    return {"detail": "Database deleted"}


# ── Versions ─────────────────────────────────────────────────

@router.get("/databases/{database_id}/versions", response_model=list[VersionResponse])
async def list_versions(
    database_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all versions for a database."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Database not found")

    versions = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == database_id
        ).order_by(DatabaseVersion.version_number.desc())
    )
    return [
        VersionResponse(
            id=str(v.id), version_number=v.version_number, file_hash=v.file_hash,
            file_size=v.file_size, notes=v.notes,
            created_by=str(v.created_by) if v.created_by else None,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
        for v in versions.scalars().all()
    ]


@router.get("/databases/{database_id}/versions/{version_number}", response_model=VersionResponse)
async def get_version(
    database_id: str,
    version_number: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a specific version."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Database not found")

    ver = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == database_id,
            DatabaseVersion.version_number == version_number,
        )
    )
    ver = ver.scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")

    return VersionResponse(
        id=str(ver.id), version_number=ver.version_number, file_hash=ver.file_hash,
        file_size=ver.file_size, notes=ver.notes,
        created_by=str(ver.created_by) if ver.created_by else None,
        created_at=ver.created_at.isoformat() if ver.created_at else "",
    )


@router.get("/databases/{database_id}/diff", response_model=DiffResponse)
async def diff_versions(
    database_id: str,
    version_a: int = Query(..., description="First version number"),
    version_b: int = Query(..., description="Second version number"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare two versions of a database."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Database not found")

    va = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == database_id,
            DatabaseVersion.version_number == version_a,
        )
    )
    va = va.scalar_one_or_none()

    vb = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == database_id,
            DatabaseVersion.version_number == version_b,
        )
    )
    vb = vb.scalar_one_or_none()

    if not va or not vb:
        raise HTTPException(status_code=404, detail="Version not found")

    return DiffResponse(
        version_a=va.version_number,
        version_b=vb.version_number,
        hash_a=va.file_hash,
        hash_b=vb.file_hash,
        size_a=va.file_size,
        size_b=vb.file_size,
        identical=(va.file_hash == vb.file_hash),
    )


# ── Backups ─────────────────────────────────────────────────

@router.post("/databases/{database_id}/backups", response_model=BackupResponse, status_code=201)
async def create_backup(
    database_id: str,
    body: BackupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a backup at the current version."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    if paradox_db.latest_version == 0:
        raise HTTPException(status_code=400, detail="No versions to backup")

    backup = DatabaseBackup(
        id=uuid.uuid4(),
        db_id=uuid.UUID(database_id) if isinstance(database_id, str) else database_id,
        user_id=user.id,
        name=body.name,
        notes=body.notes,
        version_number=paradox_db.latest_version,
        file_hash=paradox_db.file_hash or "",
        file_size=0,
        message_id=paradox_db.latest_message_id,
        created_at=datetime.utcnow(),
    )

    # Get file size from version history
    ver_result = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == database_id,
            DatabaseVersion.version_number == paradox_db.latest_version,
        )
    )
    ver = ver_result.scalar_one_or_none()
    if ver:
        backup.file_size = ver.file_size

    db.add(backup)
    await db.flush()

    return BackupResponse(
        id=str(backup.id), name=backup.name, notes=backup.notes,
        version_number=backup.version_number, file_hash=backup.file_hash,
        file_size=backup.file_size,
        created_at=backup.created_at.isoformat(),
    )


@router.get("/databases/{database_id}/backups", response_model=list[BackupResponse])
async def list_backups(
    database_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all backups for a database."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Database not found")

    backups = await db.execute(
        select(DatabaseBackup).where(
            DatabaseBackup.db_id == database_id,
            DatabaseBackup.user_id == user.id,
        ).order_by(DatabaseBackup.created_at.desc())
    )
    return [
        BackupResponse(
            id=str(b.id), name=b.name, notes=b.notes,
            version_number=b.version_number, file_hash=b.file_hash,
            file_size=b.file_size,
            created_at=b.created_at.isoformat() if b.created_at else "",
        )
        for b in backups.scalars().all()
    ]


@router.post("/databases/{database_id}/backups/{backup_id}/restore")
async def restore_backup(
    database_id: str,
    backup_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore a database to a backup version. Returns the file for download."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    backup = await db.execute(
        select(DatabaseBackup).where(
            DatabaseBackup.id == backup_id,
            DatabaseBackup.db_id == database_id,
            DatabaseBackup.user_id == user.id,
        )
    )
    backup = backup.scalar_one_or_none()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    # Find the version to restore
    ver_result = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == database_id,
            DatabaseVersion.version_number == backup.version_number,
        )
    )
    ver = ver_result.scalar_one_or_none()
    if not ver or not ver.message_id:
        raise HTTPException(status_code=404, detail="Version data not found in Telegram")

    # Download from Telegram
    tg = TelegramClient(
        bot_token=settings.telegram_bot_token,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    try:
        file_bytes = await tg.download_file(
            channel_id=settings.telegram_storage_chat_id,
            message_id=ver.message_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to download from Telegram: {e}")

    # Upload as new version
    new_version = paradox_db.latest_version + 1
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    caption = {
        "db_name": paradox_db.name,
        "version": new_version,
        "type": "restore",
        "backup_name": backup.name,
        "timestamp": datetime.utcnow().isoformat(),
        "hash": file_hash,
        "user_id": str(user.id),
    }

    tg_upload = TelegramClient(
        bot_token=settings.telegram_bot_token,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    try:
        msg_id = await tg_upload.upload_file(
            settings.telegram_storage_chat_id, file_bytes, caption
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upload after restore failed: {e}")

    # Update DB record
    paradox_db.latest_version = new_version
    paradox_db.latest_message_id = msg_id
    paradox_db.file_hash = file_hash
    paradox_db.updated_at = datetime.utcnow()

    # Create version record
    version_record = DatabaseVersion(
        id=uuid.uuid4(),
        db_id=uuid.UUID(database_id) if isinstance(database_id, str) else database_id,
        version_number=new_version,
        file_hash=file_hash,
        file_size=len(file_bytes),
        message_id=msg_id,
        notes=f"Restored from backup '{backup.name}' (v{backup.version_number})",
        created_by=user.id,
        created_at=datetime.utcnow(),
    )
    db.add(version_record)

    log_entry = SyncLog(
        request_id=str(uuid.uuid4()),
        user_id=user.id,
        database_name=paradox_db.name,
        operation="restore",
        telegram_message_id=msg_id,
        status="success",
        completed_at=datetime.utcnow(),
    )
    db.add(log_entry)

    return {
        "detail": f"Restored to v{new_version} from backup '{backup.name}'",
        "version": new_version,
        "message_id": msg_id,
    }


# ── Upload / Download (sync endpoints, reworked) ─────────────

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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a database file. Accepts database_id or project_id + database_name."""
    start = time.time()
    uid = user.id

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    database_id = body.get("database_id", "")
    project_id = body.get("project_id", "")
    database_name = body.get("database_name", "")
    file_data_b64 = body.get("file_data", "")
    changeset_data_b64 = body.get("changeset_data", "")
    version_type = body.get("version_type", "auto")
    client_version = body.get("version")
    encryption_key = body.get("encryption_key", "")
    storage_chat_id = body.get("storage_chat_id", "")
    log_chat_id = body.get("log_chat_id", "")

    # Resolve database_id from project_id + name if needed
    if not database_id and project_id and database_name:
        result = await db.execute(
            select(ParadoxDB).where(
                ParadoxDB.project_id == project_id,
                ParadoxDB.user_id == uid,
                ParadoxDB.name == database_name,
            )
        )
        paradox_db = result.scalar_one_or_none()
        if paradox_db:
            database_id = str(paradox_db.id)

    if not database_id and not database_name:
        return JSONResponse(status_code=400, content={"error": "database_id or database_name required"})

    # Try to find by name for backward compat
    if not database_id:
        result = await db.execute(
            select(ParadoxDB).where(
                ParadoxDB.user_id == uid,
                ParadoxDB.name == database_name,
            )
        )
        paradox_db = result.scalar_one_or_none()
        if paradox_db:
            database_id = str(paradox_db.id)

    if not database_id:
        return JSONResponse(status_code=404, content={"error": "database_not_found", "detail": "Create the database first via POST /v1/projects/{id}/databases"})

    # Get the database record
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == uid)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        return JSONResponse(status_code=404, content={"error": "database_not_found"})

    # Decode file data
    if changeset_data_b64:
        try:
            file_bytes = base64.b64decode(changeset_data_b64)
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid base64"})
    elif file_data_b64:
        try:
            file_bytes = base64.b64decode(file_data_b64)
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid base64"})
    else:
        return JSONResponse(status_code=400, content={"error": "missing file_data"})

    # Optional gateway-side encryption
    if encryption_key:
        from ..crypto import encrypt_data
        file_bytes = encrypt_data(file_bytes, encryption_key)

    if not rate_limiter.check(uid):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})

    # Version conflict check
    if client_version is not None and client_version < paradox_db.latest_version:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict_detected",
                "remote_version": paradox_db.latest_version,
                "your_version": client_version,
                "resolution": "pull_before_push",
            },
        )

    # Lock
    lock_key = f"{uid}:{database_id}"
    try:
        acquired = await _upload_lock.acquire(lock_key, timeout=settings.lock_timeout_seconds)
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": "lock_error", "detail": str(e)})
    if not acquired:
        return JSONResponse(status_code=503, content={"error": "lock_timeout"})

    try:
        new_version = paradox_db.latest_version + 1
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # Upload to Telegram
        tg = TelegramClient(
            bot_token=settings.telegram_bot_token,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
        caption = {
            "db_name": paradox_db.name,
            "version": new_version,
            "type": version_type,
            "timestamp": datetime.utcnow().isoformat(),
            "hash": file_hash,
            "user_id": str(uid),
        }

        try:
            storage_targets = [settings.telegram_storage_chat_id]
            if storage_chat_id and storage_chat_id != settings.telegram_storage_chat_id:
                storage_targets.append(storage_chat_id)
            message_id = ""
            for idx, chat_id in enumerate(storage_targets):
                mid = await tg.upload_file(chat_id, file_bytes, caption)
                if idx == 0:
                    message_id = mid
        except TelegramRateLimitError as e:
            return JSONResponse(status_code=429, content={"error": "rate_limited", "retry_after": e.retry_after})
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": "telegram_failed", "detail": str(e)})

        # Notify the SDK's log channel (if any) in addition to the system log
        if log_chat_id:
            try:
                await log_operation(
                    "upload",
                    f"{paradox_db.name} v{new_version} stored in {', '.join(storage_targets)}",
                    "success",
                    extra_chat_ids=[log_chat_id],
                )
            except Exception:
                pass

        # Update database record
        paradox_db.latest_version = new_version
        paradox_db.latest_message_id = message_id
        paradox_db.file_hash = file_hash
        paradox_db.updated_at = datetime.utcnow()

        # Create version record
        version_record = DatabaseVersion(
            id=uuid.uuid4(),
            db_id=uuid.UUID(database_id) if isinstance(database_id, str) else database_id,
            version_number=new_version,
            file_hash=file_hash,
            file_size=len(file_bytes),
            message_id=message_id,
            created_by=uid,
            created_at=datetime.utcnow(),
        )
        db.add(version_record)

        # Log
        log_entry = SyncLog(
            request_id=str(uuid.uuid4()),
            user_id=uid,
            database_name=paradox_db.name,
            operation="upload",
            telegram_message_id=message_id,
            status="success",
            completed_at=datetime.utcnow(),
        )
        db.add(log_entry)

        # Notify SSE clients
        try:
            from .notifications import notify_user
            notify_user(str(uid), paradox_db.name, new_version, message_id)
        except Exception:
            pass

        duration_ms = (time.time() - start) * 1000
        return {
            "request_id": str(uuid.uuid4()),
            "database_id": database_id,
            "message_id": message_id,
            "version": new_version,
            "uploaded_at": datetime.utcnow().isoformat(),
            "duration_ms": round(duration_ms, 1),
        }

    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})
    finally:
        try:
            await _upload_lock.release(lock_key)
        except Exception:
            pass


@router.get("/download")
async def download(
    database_id: str = Query(default=""),
    database_name: str = Query(default=""),
    version: int | None = Query(default=None),
    encryption_key: str = Query(default=""),
    storage_chat_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a database file. Accepts database_id or database_name (legacy)."""
    # Resolve by name if no id provided
    if not database_id and database_name:
        result = await db.execute(
            select(ParadoxDB).where(
                ParadoxDB.user_id == user.id,
                ParadoxDB.name == database_name,
            )
        )
        paradox_db = result.scalar_one_or_none()
        if paradox_db:
            database_id = str(paradox_db.id)

    if not database_id:
        raise HTTPException(status_code=400, detail="database_id or database_name required")

    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.id == database_id, ParadoxDB.user_id == user.id)
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    if version is not None:
        ver_result = await db.execute(
            select(DatabaseVersion).where(
                DatabaseVersion.db_id == database_id,
                DatabaseVersion.version_number == version,
            )
        )
        ver = ver_result.scalar_one_or_none()
        if not ver:
            raise HTTPException(status_code=404, detail="Version not found")
        message_id = ver.message_id
        resolved_version = ver.version_number
    else:
        message_id = paradox_db.latest_message_id
        resolved_version = paradox_db.latest_version

    if not message_id:
        raise HTTPException(status_code=404, detail="No file data available")

    tg = TelegramClient(
        bot_token=settings.telegram_bot_token,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    try:
        file_bytes = await tg.download_file(
            channel_id=storage_chat_id or settings.telegram_storage_chat_id,
            message_id=message_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Telegram download failed: {e}")

    # Optional decryption
    if encryption_key:
        from ..crypto import decrypt_data
        file_bytes = decrypt_data(file_bytes, encryption_key)

    return Response(
        content=file_bytes,
        media_type="application/octet-stream",
        headers={
            "X-Database-ID": database_id,
            "X-Version": str(resolved_version),
            "X-Message-ID": message_id,
        },
    )


# ── Legacy compatibility endpoints ─────────────────────────────

@router.get("/status")
async def legacy_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy status endpoint — returns all databases for user."""
    result = await db.execute(
        select(ParadoxDB).where(ParadoxDB.user_id == user.id)
    )
    databases = result.scalars().all()
    return {
        "user_id": str(user.id),
        "databases": [
            {
                "name": d.name,
                "latest_version": d.latest_version,
                "latest_message_id": d.latest_message_id or "",
                "pending_changesets": 0,
                "last_sync_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in databases
        ],
    }


@router.get("/versions")
async def legacy_versions(
    database_name: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy versions endpoint — list versions by database name."""
    result = await db.execute(
        select(ParadoxDB).where(
            ParadoxDB.user_id == user.id,
            ParadoxDB.name == database_name,
        )
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        raise HTTPException(status_code=404, detail="Database not found")

    versions = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == str(paradox_db.id)
        ).order_by(DatabaseVersion.version_number.desc())
    )
    return {
        "database_name": database_name,
        "versions": [
            {
                "version": v.version_number,
                "message_id": v.message_id or "",
                "uploaded_at": v.created_at.isoformat() if v.created_at else "",
                "size_bytes": v.file_size,
            }
            for v in versions.scalars().all()
        ],
    }


@router.post("/rollback")
async def legacy_rollback(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy rollback — download a previous version and re-upload as new."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    database_name = body.get("database_name", "")
    target_version = body.get("target_version")
    storage_chat_id = body.get("storage_chat_id", "")

    if not database_name or not target_version:
        return JSONResponse(status_code=400, content={"error": "database_name and target_version required"})

    # Find database
    result = await db.execute(
        select(ParadoxDB).where(
            ParadoxDB.user_id == user.id,
            ParadoxDB.name == database_name,
        )
    )
    paradox_db = result.scalar_one_or_none()
    if not paradox_db:
        return JSONResponse(status_code=404, content={"error": "database_not_found"})

    # Find target version
    ver_result = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.db_id == str(paradox_db.id),
            DatabaseVersion.version_number == target_version,
        )
    )
    ver = ver_result.scalar_one_or_none()
    if not ver or not ver.message_id:
        return JSONResponse(status_code=404, content={"error": "version_not_found"})

    # Download from Telegram
    tg = TelegramClient(
        bot_token=settings.telegram_bot_token,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    try:
        file_bytes = await tg.download_file(
            channel_id=storage_chat_id or settings.telegram_storage_chat_id,
            message_id=ver.message_id,
        )
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "telegram_failed", "detail": str(e)})

    # Re-upload as new version
    new_version = paradox_db.latest_version + 1
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    caption = {
        "db_name": paradox_db.name,
        "version": new_version,
        "type": "rollback",
        "timestamp": datetime.utcnow().isoformat(),
        "hash": file_hash,
        "user_id": str(user.id),
    }

    try:
        targets = [settings.telegram_storage_chat_id]
        if storage_chat_id and storage_chat_id != settings.telegram_storage_chat_id:
            targets.append(storage_chat_id)
        msg_id = ""
        for idx, chat_id in enumerate(targets):
            mid = await tg.upload_file(chat_id, file_bytes, caption)
            if idx == 0:
                msg_id = mid
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "telegram_upload_failed", "detail": str(e)})

    # Update records
    paradox_db.latest_version = new_version
    paradox_db.latest_message_id = msg_id
    paradox_db.file_hash = file_hash
    paradox_db.updated_at = datetime.utcnow()

    version_record = DatabaseVersion(
        id=uuid.uuid4(),
        db_id=uuid.UUID(str(paradox_db.id)),
        version_number=new_version,
        file_hash=file_hash,
        file_size=len(file_bytes),
        message_id=msg_id,
        notes=f"Rollback to v{target_version}",
        created_by=user.id,
        created_at=datetime.utcnow(),
    )
    db.add(version_record)

    log_entry = SyncLog(
        request_id=str(uuid.uuid4()),
        user_id=user.id,
        database_name=paradox_db.name,
        operation="rollback",
        telegram_message_id=msg_id,
        status="success",
        completed_at=datetime.utcnow(),
    )
    db.add(log_entry)

    return {
        "request_id": str(uuid.uuid4()),
        "rolled_back_to": target_version,
        "new_message_id": msg_id,
    }
