import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# SQLAlchemy ORM Models
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    api_key_hash = Column(String(64), unique=True, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    projects = relationship("Project", back_populates="user", lazy="selectin")
    databases = relationship("ParadoxDB", back_populates="user", lazy="selectin", foreign_keys="ParadoxDB.user_id")
    sync_logs = relationship("SyncLog", back_populates="user", lazy="selectin")
    conflict_logs = relationship("ConflictLog", back_populates="user", lazy="selectin")


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="projects")
    databases = relationship("ParadoxDB", back_populates="project", lazy="selectin")


class ParadoxDB(Base):
    __tablename__ = "paradox_dbs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    latest_version = Column(Integer, default=0, nullable=False)
    latest_message_id = Column(String(64), nullable=True)
    file_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="databases")
    user = relationship("User", back_populates="databases", foreign_keys=[user_id])
    versions = relationship("DatabaseVersion", back_populates="db", lazy="selectin", order_by="DatabaseVersion.version_number.desc()")
    backups = relationship("DatabaseBackup", back_populates="db", lazy="selectin")


class DatabaseVersion(Base):
    __tablename__ = "database_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    db_id = Column(UUID(as_uuid=True), ForeignKey("paradox_dbs.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=False)
    file_size = Column(Integer, default=0, nullable=False)
    message_id = Column(String(64), nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    db = relationship("ParadoxDB", back_populates="versions")
    creator = relationship("User", foreign_keys=[created_by])


class DatabaseBackup(Base):
    __tablename__ = "database_backups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    db_id = Column(UUID(as_uuid=True), ForeignKey("paradox_dbs.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    notes = Column(Text, nullable=True)
    version_number = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=False)
    file_size = Column(Integer, default=0, nullable=False)
    message_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    db = relationship("ParadoxDB", back_populates="backups")
    user = relationship("User")


class SyncLog(Base):
    __tablename__ = "sync_log"

    request_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    database_name = Column(String(255), nullable=False)
    operation = Column(String(32), nullable=False)
    telegram_message_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="sync_logs")


class ConflictLog(Base):
    __tablename__ = "conflict_log"

    conflict_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    database_name = Column(String(255), nullable=False)
    local_version = Column(Integer, nullable=False)
    remote_version = Column(Integer, nullable=False)
    resolution = Column(String(32), nullable=False, default="lww")
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="conflict_logs")


# ---------------------------------------------------------------------------
# Pydantic Response / Request Models
# ---------------------------------------------------------------------------


# -- Auth --


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: str
    email: str
    username: str
    access_token: str
    token_type: str = "bearer"
    api_key: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    is_active: bool
    created_at: str


# -- Projects --


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    database_count: int = 0
    created_at: str
    updated_at: str


# -- Databases --


class DatabaseCreate(BaseModel):
    name: str
    description: str | None = None


class DatabaseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class DatabaseResponse(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    latest_version: int
    file_hash: str | None
    created_at: str
    updated_at: str


# -- Versions --


class VersionResponse(BaseModel):
    id: str
    version_number: int
    file_hash: str
    file_size: int
    notes: str | None
    created_by: str | None
    created_at: str


# -- Backups --


class BackupCreate(BaseModel):
    name: str
    notes: str | None = None


class BackupResponse(BaseModel):
    id: str
    name: str
    notes: str | None
    version_number: int
    file_hash: str
    file_size: int
    created_at: str


# -- Diff --


class DiffResponse(BaseModel):
    version_a: int
    version_b: int
    hash_a: str
    hash_b: str
    size_a: int
    size_b: int
    identical: bool


# -- Legacy / compatibility models (kept from original) --


class UploadRequest(BaseModel):
    database_name: str
    version_type: str = "auto"
    version: int | None = None


class UploadResponse(BaseModel):
    request_id: str
    message_id: str
    version: int
    uploaded_at: str


class DownloadQuery(BaseModel):
    database_name: str
    version: int | None = None


class VersionsQuery(BaseModel):
    database_name: str


class VersionInfo(BaseModel):
    version: int
    message_id: str
    uploaded_at: str
    size_bytes: int


class VersionsResponse(BaseModel):
    database_name: str
    versions: list[VersionInfo]


class RollbackRequest(BaseModel):
    database_name: str
    target_version: int


class RollbackResponse(BaseModel):
    request_id: str
    rolled_back_to: int
    new_message_id: str


class StatusResponse(BaseModel):
    user_id: str
    databases: list["DatabaseStatusInfo"]


class DatabaseStatusInfo(BaseModel):
    name: str
    latest_version: int
    latest_message_id: str
    pending_changesets: int
    last_sync_at: str | None


class ConflictResponse(BaseModel):
    error: str = "conflict_detected"
    remote_version: int
    remote_message_id: str
    your_version: int
    resolution: str = "pull_before_push"


class RateLimitResponse(BaseModel):
    error: str = "rate_limited"
    retry_after_seconds: int
    queue_depth: int


class NotFoundResponse(BaseModel):
    error: str = "not_found"
    database_name: str


class RegisterResponse(BaseModel):
    user_id: str
    api_key: str
    channel_id: str


class HealthResponse(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
