import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth import generate_api_key, hash_api_key
from app.models import UserChannel, DatabaseVersion, SyncLog
from app.database import get_db

import base64
import json


client = TestClient(app)


def _register_user() -> dict:
    resp = client.post("/v1/auth/register")
    return resp.json()


def _auth_header(api_key: str) -> dict:
    return {"X-API-Key": api_key}


# ── POST /upload ───────────────────────────────────────────────


@patch("app.routers.upload.TelegramClient")
def test_upload_without_auth_returns_401(MockTG):
    resp = client.post("/v1/upload", json={"database_name": "test.db", "file_data": "dGVzdA=="})
    assert resp.status_code == 401


@patch("app.routers.upload.TelegramClient")
def test_upload_missing_database_name_returns_400(MockTG):
    user = _register_user()
    resp = client.post(
        "/v1/upload",
        json={"file_data": "dGVzdA=="},
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 400
    assert "missing database_name" in resp.json()["error"]


@patch("app.routers.upload.TelegramClient")
def test_upload_missing_file_data_returns_400(MockTG):
    user = _register_user()
    resp = client.post(
        "/v1/upload",
        json={"database_name": "test.db"},
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 400


@patch("app.routers.upload.TelegramClient")
def test_upload_invalid_base64_returns_400(MockTG):
    user = _register_user()
    resp = client.post(
        "/v1/upload",
        json={"database_name": "test.db", "file_data": "not-valid-base64!@#"},
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 400


@patch("app.routers.upload.TelegramClient")
def test_upload_success_returns_200(MockTG):
    mock_instance = MockTG.return_value
    mock_instance.upload_file = AsyncMock(return_value="12345")

    user = _register_user()
    file_b64 = base64.b64encode(b"fake sqlite data").decode()

    resp = client.post(
        "/v1/upload",
        json={
            "database_name": "test.db",
            "file_data": file_b64,
            "version_type": "full",
        },
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "request_id" in data
    assert data["message_id"] == "12345"
    assert data["version"] == 1
    assert "uploaded_at" in data


@patch("app.routers.upload.TelegramClient")
def test_upload_computes_sha256_hash(MockTG):
    mock_instance = MockTG.return_value
    mock_instance.upload_file = AsyncMock(return_value="100")

    user = _register_user()
    file_bytes = b"hello paradox"
    file_b64 = base64.b64encode(file_bytes).decode()

    resp = client.post(
        "/v1/upload",
        json={"database_name": "hash_test.db", "file_data": file_b64},
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 200


@patch("app.routers.upload.TelegramClient")
def test_upload_increments_version_on_second_upload(MockTG):
    mock_instance = MockTG.return_value
    mock_instance.upload_file = AsyncMock(return_value="200")

    user = _register_user()
    file_b64 = base64.b64encode(b"v1 data").decode()

    resp1 = client.post(
        "/v1/upload",
        json={"database_name": "ver.db", "file_data": file_b64},
        headers=_auth_header(user["api_key"]),
    )
    assert resp1.json()["version"] == 1

    file_b64_2 = base64.b64encode(b"v2 data").decode()
    resp2 = client.post(
        "/v1/upload",
        json={"database_name": "ver.db", "file_data": file_b64_2},
        headers=_auth_header(user["api_key"]),
    )
    assert resp2.json()["version"] == 2


@patch("app.routers.upload.TelegramClient")
def test_upload_conflict_returns_409(MockTG):
    mock_instance = MockTG.return_value
    mock_instance.upload_file = AsyncMock(return_value="300")

    user = _register_user()
    file_b64 = base64.b64encode(b"data").decode()

    # First upload sets version to 1
    client.post(
        "/v1/upload",
        json={"database_name": "conflict.db", "file_data": file_b64},
        headers=_auth_header(user["api_key"]),
    )

    # Second upload with client_version=0 (behind) should 409
    resp = client.post(
        "/v1/upload",
        json={
            "database_name": "conflict.db",
            "file_data": file_b64,
            "version": 0,
        },
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "conflict_detected"
    assert data["remote_version"] == 1
    assert data["your_version"] == 0


@patch("app.routers.upload.TelegramClient")
def test_upload_logs_to_sync_log(MockTG):
    mock_instance = MockTG.return_value
    mock_instance.upload_file = AsyncMock(return_value="500")

    user = _register_user()
    file_b64 = base64.b64encode(b"logged data").decode()

    resp = client.post(
        "/v1/upload",
        json={"database_name": "log_test.db", "file_data": file_b64},
        headers=_auth_header(user["api_key"]),
    )
    assert resp.status_code == 200
    request_id = resp.json()["request_id"]

    # Verify sync_log entry exists
    from app.database import async_session

    import asyncio

    async def check_log():
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(SyncLog).where(SyncLog.request_id == request_id)
            )
            log = result.scalar_one_or_none()
            assert log is not None
            assert log.status == "success"
            assert log.operation == "upload"
            assert log.telegram_message_id == "500"

    asyncio.get_event_loop().run_until_complete(check_log())
