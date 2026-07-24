import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.auth import generate_api_key, hash_api_key

client = TestClient(app)


def _register() -> dict:
    return client.post("/v1/auth/register").json()


def _auth(api_key: str) -> dict:
    return {"X-API-Key": api_key}


@patch("app.routers.upload.TelegramClient")
def test_upload_conflict_returns_409(MockTG):
    mock = MockTG.return_value
    mock.upload_file = AsyncMock(return_value="100")

    user = _register()
    import base64
    data = base64.b64encode(b"v1").decode()

    # First upload — sets version to 1
    r1 = client.post(
        "/v1/upload",
        json={"database_name": "conflict.db", "file_data": data},
        headers=_auth(user["api_key"]),
    )
    assert r1.status_code == 200

    # Second upload with client_version=0 (behind)
    r2 = client.post(
        "/v1/upload",
        json={"database_name": "conflict.db", "file_data": data, "version": 0},
        headers=_auth(user["api_key"]),
    )
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"] == "conflict_detected"
    assert body["remote_version"] == 1
    assert body["your_version"] == 0
    assert "remote_message_id" in body
    assert body["resolution"] == "pull_before_push"


@patch("app.routers.upload.TelegramClient")
def test_upload_no_conflict_when_versions_match(MockTG):
    mock = MockTG.return_value
    mock.upload_file = AsyncMock(return_value="200")

    user = _register()
    import base64
    data = base64.b64encode(b"data").decode()

    r1 = client.post(
        "/v1/upload",
        json={"database_name": "match.db", "file_data": data},
        headers=_auth(user["api_key"]),
    )
    assert r1.status_code == 200
    version = r1.json()["version"]

    r2 = client.post(
        "/v1/upload",
        json={"database_name": "match.db", "file_data": data, "version": version},
        headers=_auth(user["api_key"]),
    )
    assert r2.status_code == 200
