"""Pydantic models for parad types."""

from pydantic import BaseModel


# ── Config ──────────────────────────────────────────────────────

class EncryptionConfig(BaseModel):
    cipher: str = "aes-256-cbc"
    kdf_iterations: int = 256000
    page_size: int = 4096


class SyncConfig(BaseModel):
    gateway_url: str = "https://paradox-db.onrender.com/v1"
    api_key: str = ""
    trigger_timer_seconds: int = 30
    trigger_ops_threshold: int = 50
    max_file_size_mb: int = 50
    auto_sync_on_shutdown: bool = True
    auto_watch: bool = False
    watch_interval_seconds: int = 30


class ConflictConfig(BaseModel):
    strategy: str = "last-write-wins"
    log_conflicts: bool = True


class LoggingConfig(BaseModel):
    level: str = "info"
    path: str = "~/.paradox/logs"


class Config(BaseModel):
    database_path: str = "~/.paradox/data.db"
    encryption: EncryptionConfig = EncryptionConfig()
    sync: SyncConfig = SyncConfig()
    conflict: ConflictConfig = ConflictConfig()
    logging: LoggingConfig = LoggingConfig()


# ── API Responses ───────────────────────────────────────────────

class RegisterResponse(BaseModel):
    user_id: str
    api_key: str
    jwt: str


class UploadResponse(BaseModel):
    request_id: str
    message_id: str
    version: int
    uploaded_at: str


class DatabaseStatus(BaseModel):
    name: str
    latest_version: int
    latest_message_id: str
    pending_changesets: int = 0
    last_sync_at: str | None = None


class StatusResponse(BaseModel):
    user_id: str
    databases: list[DatabaseStatus] = []


class VersionEntry(BaseModel):
    version: int
    message_id: str
    uploaded_at: str = ""
    size_bytes: int | None = None


class VersionsResponse(BaseModel):
    database_name: str = ""
    versions: list[VersionEntry] = []


class RollbackResponse(BaseModel):
    request_id: str = ""
    rolled_back_to: int
    new_message_id: str


class DownloadResult(BaseModel):
    """Result from gateway download — includes bytes + metadata."""
    bytes: bytes
    version: int | None = None
    message_id: str | None = None
