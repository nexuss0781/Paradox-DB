import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class UserChannel(Base):
    __tablename__ = "user_channels"

    user_id = Column(String(64), primary_key=True)
    channel_id = Column(String(64), nullable=False, default="")
    bot_token_id = Column(String(255), nullable=False, default="")
    api_key_hash = Column(String(64), nullable=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    versions = relationship("DatabaseVersion", back_populates="user_channel", lazy="selectin")
    sync_logs = relationship("SyncLog", back_populates="user_channel", lazy="selectin")


class DatabaseVersion(Base):
    __tablename__ = "database_versions"

    user_id = Column(String(64), ForeignKey("user_channels.user_id"), primary_key=True)
    database_name = Column(String(255), primary_key=True)
    latest_message_id = Column(String(64), nullable=False)
    latest_version = Column(Integer, nullable=False, default=1)
    file_hash = Column(String(64), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user_channel = relationship("UserChannel", back_populates="versions")


class SyncLog(Base):
    __tablename__ = "sync_log"

    request_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), ForeignKey("user_channels.user_id"), nullable=False)
    database_name = Column(String(255), nullable=False)
    operation = Column(String(32), nullable=False)
    telegram_message_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    user_channel = relationship("UserChannel", back_populates="sync_logs")


class ConflictLog(Base):
    __tablename__ = "conflict_log"

    conflict_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), ForeignKey("user_channels.user_id"), nullable=False)
    database_name = Column(String(255), nullable=False)
    local_version = Column(Integer, nullable=False)
    remote_version = Column(Integer, nullable=False)
    local_hash = Column(String(64), nullable=True)
    remote_hash = Column(String(64), nullable=True)
    resolution = Column(String(32), nullable=False, default="lww")
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)


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


class RegisterRequest(BaseModel):
    username: str


class RegisterResponse(BaseModel):
    user_id: str
    api_key: str
    channel_id: str


class HealthResponse(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
