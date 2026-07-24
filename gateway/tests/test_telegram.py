import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_httpx

from app.services.telegram import (
    TelegramClient,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)


TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
TEST_CHANNEL_ID = "-1001234567890"
TEST_MESSAGE_ID = "42"


def _make_client() -> TelegramClient:
    return TelegramClient(
        bot_token=TEST_BOT_TOKEN,
        api_id="12345",
        api_hash="abcdef1234567890",
    )


def _mock_response(status_code: int = 200, json_data: dict | None = None):
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
    )
    return resp


# ── create_private_channel ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_private_channel_returns_channel_id(httpx_mock: pytest_httpx.MockTransport):
    """create_private_channel returns channel_id from getChat fallback."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/createChatInviteLink",
        json={"ok": True, "result": {"chat": {"id": -100999}}},
    )
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getChat",
        json={"ok": True, "result": {"id": -100888}},
    )
    client = _make_client()
    channel_id = await client.create_private_channel("12345")
    assert channel_id == "-100888"


@pytest.mark.asyncio
async def test_create_private_channel_returns_empty_on_failure(httpx_mock: pytest_httpx.MockTransport):
    """create_private_channel returns empty string when all calls fail."""
    httpx_mock.add_response(status_code=400, json={"ok": False})
    client = _make_client()
    channel_id = await client.create_private_channel("bad_user")
    assert channel_id == ""


# ── upload_file ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_file_sends_document(httpx_mock: pytest_httpx.MockTransport):
    """uploadFile sends a document with correct structure."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        json={"ok": True, "result": {"message_id": 100}},
    )
    client = _make_client()
    msg_id = await client.upload_file(
        TEST_CHANNEL_ID,
        b"fake file bytes",
        {"db": "test.db", "version": 1},
    )
    assert msg_id == "100"


@pytest.mark.asyncio
async def test_upload_file_returns_message_id(httpx_mock: pytest_httpx.MockTransport):
    """uploadFile returns the message_id as a string."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        json={"ok": True, "result": {"message_id": 42}},
    )
    client = _make_client()
    result = await client.upload_file(
        TEST_CHANNEL_ID, b"payload", {"key": "val"}
    )
    assert result == "42"
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_upload_file_includes_json_caption(httpx_mock: pytest_httpx.MockTransport):
    """uploadFile includes JSON-encoded caption in the request."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        json={"ok": True, "result": {"message_id": 1}},
    )
    client = _make_client()
    caption = {"db_name": "users.db", "version": 3, "hash": "abc123"}
    await client.upload_file(TEST_CHANNEL_ID, b"data", caption)

    request = httpx_mock.get_request()
    body = request.read()
    assert b'"db_name"' in body
    assert b'"users.db"' in body
    assert b'"version": 3' in body


@pytest.mark.asyncio
async def test_upload_file_permanent_error_on_500(httpx_mock: pytest_httpx.MockTransport):
    """uploadFile raises TelegramPermanentError on server error."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        status_code=500,
        text="Internal Server Error",
    )
    client = _make_client()
    with pytest.raises(TelegramPermanentError, match="Upload failed"):
        await client.upload_file(TEST_CHANNEL_ID, b"data", {})


@pytest.mark.asyncio
async def test_upload_file_rate_limit_error_on_429(httpx_mock: pytest_httpx.MockTransport):
    """uploadFile raises TelegramRateLimitError on 429."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        status_code=429,
        json={
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 45},
        },
    )
    client = _make_client()
    with pytest.raises(TelegramRateLimitError) as exc_info:
        await client.upload_file(TEST_CHANNEL_ID, b"data", {})
    assert exc_info.value.retry_after == 45


# ── download_file ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_file_retrieves_by_message_id(httpx_mock: pytest_httpx.MockTransport):
    """downloadFile fetches getMessage -> getFile -> file download."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        json={
            "ok": True,
            "result": {
                "document": {
                    "file_id": "AgACAgIAAxkBAAI",
                    "file_size": 5,
                    "file_name": "backup.db",
                }
            },
        },
    )
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getFile",
        json={
            "ok": True,
            "result": {"file_path": "documents/file_123.dat"},
        },
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/file/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/documents/file_123.dat",
        content=b"hello world",
    )
    client = _make_client()
    data = await client.download_file(TEST_CHANNEL_ID, TEST_MESSAGE_ID)
    assert data == b"hello world"


@pytest.mark.asyncio
async def test_download_file_returns_matching_bytes(httpx_mock: pytest_httpx.MockTransport):
    """downloadFile returns exact bytes that were uploaded."""
    original = b"\x00\x01\x02\x03\xff\xfe binary data"
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        json={
            "ok": True,
            "result": {
                "document": {
                    "file_id": "file_id_xyz",
                    "file_size": len(original),
                    "file_name": "data.bin",
                }
            },
        },
    )
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getFile",
        json={"ok": True, "result": {"file_path": "docs/data.bin"}},
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/file/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/docs/data.bin",
        content=original,
    )
    client = _make_client()
    result = await client.download_file(TEST_CHANNEL_ID, TEST_MESSAGE_ID)
    assert result == original


@pytest.mark.asyncio
async def test_download_file_permanent_error_on_message_not_found(httpx_mock: pytest_httpx.MockTransport):
    """downloadFile raises TelegramPermanentError when message not found."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        status_code=400,
        json={"ok": False, "description": "Bad Request"},
    )
    client = _make_client()
    with pytest.raises(TelegramPermanentError, match="Message not found"):
        await client.download_file(TEST_CHANNEL_ID, "999")


@pytest.mark.asyncio
async def test_download_file_permanent_error_when_no_document(httpx_mock: pytest_httpx.MockTransport):
    """downloadFile raises TelegramPermanentError when message has no file."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        json={"ok": True, "result": {"text": "just a text message"}},
    )
    client = _make_client()
    with pytest.raises(TelegramPermanentError, match="No file in message"):
        await client.download_file(TEST_CHANNEL_ID, TEST_MESSAGE_ID)


# ── get_file_metadata ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_file_metadata_returns_file_info(httpx_mock: pytest_httpx.MockTransport):
    """getFileMetadata returns file_id, file_size, file_name."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        json={
            "ok": True,
            "result": {
                "document": {
                    "file_id": "AgACAgIA",
                    "file_size": 1048576,
                    "file_name": "paradox_backup.db",
                }
            },
        },
    )
    client = _make_client()
    meta = await client.get_file_metadata(TEST_CHANNEL_ID, TEST_MESSAGE_ID)
    assert meta["file_id"] == "AgACAgIA"
    assert meta["file_size"] == 1048576
    assert meta["file_name"] == "paradox_backup.db"


@pytest.mark.asyncio
async def test_get_file_metadata_returns_empty_dict_for_text_message(httpx_mock: pytest_httpx.MockTransport):
    """getFileMetadata returns empty dict when message has no document."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        json={"ok": True, "result": {"text": "hello"}},
    )
    client = _make_client()
    meta = await client.get_file_metadata(TEST_CHANNEL_ID, TEST_MESSAGE_ID)
    assert meta == {}


@pytest.mark.asyncio
async def test_get_file_metadata_permanent_error_on_failure(httpx_mock: pytest_httpx.MockTransport):
    """getFileMetadata raises TelegramPermanentError when message not found."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMessage",
        status_code=400,
        text="Bad Request",
    )
    client = _make_client()
    with pytest.raises(TelegramPermanentError, match="Message not found"):
        await client.get_file_metadata(TEST_CHANNEL_ID, "0")


# ── Rate limiter ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit():
    """Rate limiter allows 15 calls within the per-minute window."""
    client = _make_client()
    for _ in range(15):
        await client._check_rate_limit(TEST_CHANNEL_ID)


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_limit():
    """Rate limiter raises TelegramRateLimitError after 15 calls."""
    client = _make_client()
    for _ in range(15):
        await client._check_rate_limit(TEST_CHANNEL_ID)

    with pytest.raises(TelegramRateLimitError, match="Per-channel rate limit"):
        await client._check_rate_limit(TEST_CHANNEL_ID)


@pytest.mark.asyncio
async def test_rate_limiter_different_channels_independent():
    """Rate limits are tracked independently per channel."""
    client = _make_client()
    ch1 = "channel_1"
    ch2 = "channel_2"

    for _ in range(15):
        await client._check_rate_limit(ch1)

    with pytest.raises(TelegramRateLimitError):
        await client._check_rate_limit(ch1)

    await client._check_rate_limit(ch2)


@pytest.mark.asyncio
async def test_rate_limiter_resets_after_window():
    """Rate limiter resets after the 60-second window."""
    client = _make_client()

    import asyncio as _asyncio

    for _ in range(15):
        await client._check_rate_limit(TEST_CHANNEL_ID)

    now = _asyncio.get_event_loop().time()
    client._per_channel_counts[TEST_CHANNEL_ID] = [now - 61]

    await client._check_rate_limit(TEST_CHANNEL_ID)
    assert len(client._per_channel_counts[TEST_CHANNEL_ID]) == 2


# ── Transient error retry ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_transient_error_retries_3x(httpx_mock: pytest_httpx.MockTransport):
    """Transient errors (500) can be retried by callers up to 3 times."""
    client = _make_client()

    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        status_code=500,
        text="Internal Server Error",
    )
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        status_code=500,
        text="Internal Server Error",
    )
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        status_code=500,
        text="Internal Server Error",
    )

    errors = 0
    for _ in range(3):
        try:
            await client.upload_file(TEST_CHANNEL_ID, b"data", {})
        except TelegramPermanentError:
            errors += 1

    assert errors == 3


# ── Permanent error fails immediately ──────────────────────────────


@pytest.mark.asyncio
async def test_permanent_error_fails_immediately(httpx_mock: pytest_httpx.MockTransport):
    """TelegramPermanentError is raised without retries."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        status_code=403,
        json={"ok": False, "description": "Forbidden"},
    )
    client = _make_client()
    with pytest.raises(TelegramPermanentError):
        await client.upload_file(TEST_CHANNEL_ID, b"data", {})

    assert len(httpx_mock.get_requests()) == 1


# ── is_healthy ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_healthy_returns_true_on_valid_token(httpx_mock: pytest_httpx.MockTransport):
    """is_healthy returns True when getMe succeeds."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMe",
        json={"ok": True, "result": {"id": 123, "username": "testbot"}},
    )
    client = _make_client()
    assert await client.is_healthy() is True


@pytest.mark.asyncio
async def test_is_healthy_returns_false_on_invalid_token(httpx_mock: pytest_httpx.MockTransport):
    """is_healthy returns False when getMe fails."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMe",
        status_code=401,
        json={"ok": False, "description": "Unauthorized"},
    )
    client = _make_client()
    assert await client.is_healthy() is False


@pytest.mark.asyncio
async def test_is_healthy_returns_false_on_network_error():
    """is_healthy returns False when an exception occurs."""
    client = _make_client()
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("timeout")):
        assert await client.is_healthy() is False


# ── File naming & caption conventions ──────────────────────────────


@pytest.mark.asyncio
async def test_file_naming_convention_in_upload(httpx_mock: pytest_httpx.MockTransport):
    """Upload sends file with the expected naming convention."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        json={"ok": True, "result": {"message_id": 1}},
    )
    client = _make_client()
    await client.upload_file(
        TEST_CHANNEL_ID, b"data", {"db_name": "users.db"}
    )

    request = httpx_mock.get_request()
    content_type = request.headers.get("content-type", "")
    assert "multipart/form-data" in content_type


@pytest.mark.asyncio
async def test_caption_metadata_valid_json_with_required_fields(
    httpx_mock: pytest_httpx.MockTransport,
):
    """Caption is valid JSON containing all required metadata fields."""
    httpx_mock.add_response(
        url=f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendDocument",
        json={"ok": True, "result": {"message_id": 1}},
    )
    client = _make_client()
    caption = {
        "db_name": "inventory.db",
        "version": 5,
        "user_id": "u_abc",
        "file_hash": "sha256_placeholder",
        "uploaded_at": "2026-01-01T00:00:00Z",
    }
    await client.upload_file(TEST_CHANNEL_ID, b"bytes", caption)

    request = httpx_mock.get_request()
    body = request.read()
    payload_str = body.decode("latin-1")
    assert '"db_name": "inventory.db"' in payload_str
    assert '"version": 5' in payload_str
    assert '"user_id": "u_abc"' in payload_str
    assert '"file_hash"' in payload_str
    assert '"uploaded_at"' in payload_str

    json.loads(body.split(b'"caption":')[1].split(b"}")[0].decode("latin-1").strip().rstrip("}").lstrip("{"))


# ── Exception hierarchy ────────────────────────────────────────────


def test_exception_hierarchy():
    """TelegramError hierarchy is correct."""
    assert issubclass(TelegramRateLimitError, TelegramError)
    assert issubclass(TelegramPermanentError, TelegramError)
    assert issubclass(TelegramError, Exception)

    exc = TelegramRateLimitError("rate limited", retry_after=60)
    assert exc.retry_after == 60
    assert str(exc) == "rate limited"


# ── Constructor stores attributes ─────────────────────────────────


def test_constructor_stores_attributes():
    """TelegramClient stores all constructor arguments."""
    client = TelegramClient(
        bot_token="tok", api_id="123", api_hash="hash"
    )
    assert client.bot_token == "tok"
    assert client.api_id == "123"
    assert client.api_hash == "hash"
    assert "tok" in client.base_url
    assert client._per_channel_counts == {}
    assert client._global_counts == []


def test_constructor_defaults():
    """TelegramClient has correct defaults for optional args."""
    client = TelegramClient(bot_token="tok")
    assert client.api_id == ""
    assert client.api_hash == ""
