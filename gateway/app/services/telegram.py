import asyncio
import json
from datetime import UTC, datetime

import httpx


class TelegramError(Exception):
    """Base exception for Telegram API errors.

    Carries the machine-readable `parameters` from the response (e.g.
    `retry_after`, `migrate_to_chat_id`).
    """

    error_code: int = 0

    def __init__(self, message: str, parameters: dict | None = None):
        super().__init__(message)
        self.parameters = parameters or {}


class TelegramRateLimitError(TelegramError):
    """Raised when Telegram returns 429 Too Many Requests (HTTP or local limiter)."""

    error_code = 429

    def __init__(self, message: str, retry_after: int = 30):
        super().__init__(message)
        self.retry_after = retry_after


class TelegramUnauthorizedError(TelegramError):
    """Raised on 401 — the bot token is invalid or has been revoked."""

    error_code = 401


class TelegramMigratedError(TelegramError):
    """Raised on 400 when the chat migrated to a supergroup.

    Use `migrate_to_chat_id` to re-target subsequent requests.
    """

    error_code = 400

    def __init__(self, message: str, migrate_to_chat_id: int):
        super().__init__(message)
        self.migrate_to_chat_id = migrate_to_chat_id


class TelegramForbiddenError(TelegramError):
    """Raised on 403 — blocked, kicked, no rights, or not a member."""

    error_code = 403


class TelegramNotFoundError(TelegramError):
    """Raised on 404 — the referenced object does not exist."""

    error_code = 404


class TelegramConflictError(TelegramError):
    """Raised on 409 — webhook and getUpdates used simultaneously."""

    error_code = 409


class TelegramServerError(TelegramError):
    """Raised on 5xx — Telegram API hiccup; safe to retry."""

    error_code = 500


class TelegramPermanentError(TelegramError):
    """Raised for any other non-retryable Telegram API error."""

    error_code = 400


def raise_for_status(resp, action: str) -> None:
    """Raise the matching TelegramError for a non-200 Bot API response.

    Classifies by the JSON `error_code` (falling back to the HTTP status) and
    extracts the `parameters` field (retry_after / migrate_to_chat_id).
    """
    if resp.status_code == 200:
        return
    payload = None
    try:
        payload = resp.json()
    except Exception:
        pass
    payload = payload or {}
    error_code = payload.get("error_code", resp.status_code) or resp.status_code
    description = (
        payload.get("description")
        or payload.get("message")
        or resp.text
        or f"{action} failed (HTTP {resp.status_code})"
    )
    parameters = payload.get("parameters") or {}

    if error_code == 429:
        raise TelegramRateLimitError(
            description, retry_after=parameters.get("retry_after", 30)
        )
    if error_code == 401:
        raise TelegramUnauthorizedError(description, parameters)
    if error_code == 400 and parameters.get("migrate_to_chat_id"):
        raise TelegramMigratedError(description, parameters["migrate_to_chat_id"])
    if error_code == 403:
        raise TelegramForbiddenError(description, parameters)
    if error_code == 404:
        raise TelegramNotFoundError(description, parameters)
    if error_code == 409:
        raise TelegramConflictError(description, parameters)
    if error_code >= 500:
        raise TelegramServerError(description, parameters)
    raise TelegramPermanentError(description, parameters)


class TelegramClient:
    """Client for Telegram Bot API interactions.

    Handles file upload/download via a private Telegram channel,
    rate limiting, and basic health checks.
    """

    def __init__(self, bot_token: str, api_id: str = "", api_hash: str = ""):
        self.bot_token = bot_token
        self.api_id = api_id
        self.api_hash = api_hash
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self._per_channel_counts: dict[str, list[float]] = {}
        self._global_counts: list[float] = []

    async def create_private_channel(self, user_id: str) -> str:
        """Create or resolve a private channel for the given user.

        Attempts to create a chat invite link. If the chat_id is a
        channel/supergroup, fetches its info and returns the chat id.
        Returns the channel id string, or empty string on failure.
        """
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/createChatInviteLink",
                    json={
                        "chat_id": user_id,
                        "name": f"paradox_{user_id}",
                        "expire_date": int(
                            datetime.now(UTC).timestamp()
                        )
                        + 86400 * 365,
                    },
                )
                if resp.status_code == 200:
                    result = resp.json().get("result", {})
                    chat = result.get("chat", {})
                    chat_id = chat.get("id", "")
                    if chat_id:
                        return str(chat_id)
            except (httpx.HTTPError, KeyError, TelegramError):
                pass

            try:
                resp = await client.post(
                    f"{self.base_url}/getChat",
                    json={"chat_id": user_id},
                )
                if resp.status_code == 200:
                    return str(resp.json().get("result", {}).get("id", ""))
            except (httpx.HTTPError, KeyError, TelegramError):
                pass

        return ""

    async def upload_file(
        self, channel_id: str, file_bytes: bytes, caption: dict
    ) -> str:
        """Upload a file to a channel via sendDocument.

        Returns the message_id of the sent message.
        Raises the matching TelegramError subclass (TelegramRateLimitError on 429,
        TelegramUnauthorizedError on 401, etc.) on failure.
        """
        await self._check_rate_limit(channel_id)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/sendDocument",
                data={
                    "chat_id": channel_id,
                    "caption": json.dumps(caption),
                },
                files={
                    "document": (
                        "file.dat",
                        file_bytes,
                        "application/octet-stream",
                    )
                },
            )
            if resp.status_code == 429:
                retry_after = (
                    resp.json().get("parameters", {}).get("retry_after", 30)
                )
                raise TelegramRateLimitError(
                    "Rate limited", retry_after=retry_after
                )
            if resp.status_code != 200:
                raise_for_status(resp, "upload_file")
            result = resp.json()["result"]
            file_id = ""
            doc = result.get("document")
            if doc:
                file_id = doc.get("file_id", "")
            self._last_file_id = file_id
            return str(result["message_id"])

    async def upload_file_with_file_id(
        self, channel_id: str, file_bytes: bytes, caption: dict
    ) -> tuple[str, str]:
        """Upload and return (message_id, file_id)."""
        message_id = await self.upload_file(channel_id, file_bytes, caption)
        return message_id, getattr(self, "_last_file_id", "")

    async def download_file(self, channel_id: str, message_id: str) -> bytes:
        """Download a file from a channel message by message_id.

        Uses forwardMessage to retrieve the message, then extracts the file.
        Returns raw file bytes.
        Raises the matching TelegramError subclass on failure.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/forwardMessage",
                json={
                    "chat_id": channel_id,
                    "from_chat_id": channel_id,
                    "message_id": int(message_id),
                },
            )
            if resp.status_code != 200:
                raise_for_status(resp, "download_file")
            result = resp.json().get("result", {})
            doc = result.get("document")
            if not doc:
                photo = result.get("photo")
                if photo:
                    doc = photo[-1]
            if not doc:
                raise TelegramPermanentError("No file in message")
            file_id = doc["file_id"]

            resp2 = await client.post(
                f"{self.base_url}/getFile",
                json={"file_id": file_id},
            )
            if resp2.status_code != 200:
                raise_for_status(resp2, "getFile")
            file_path = resp2.json()["result"]["file_path"]

            resp3 = await client.get(
                f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            )
            if resp3.status_code != 200:
                raise TelegramServerError(f"Download failed (HTTP {resp3.status_code})")
            return resp3.content

    async def download_file_by_id(self, file_id: str) -> bytes:
        """Download a file directly by file_id (skip message lookup)."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/getFile",
                json={"file_id": file_id},
            )
            if resp.status_code != 200:
                raise_for_status(resp, "getFile")
            file_path = resp.json()["result"]["file_path"]

            resp2 = await client.get(
                f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            )
            if resp2.status_code != 200:
                raise TelegramServerError(f"Download failed (HTTP {resp2.status_code})")
            return resp2.content

    async def get_file_metadata(
        self, channel_id: str, message_id: str
    ) -> dict:
        """Get file metadata without downloading the actual bytes.

        Returns a dict with file_id, file_size, and file_name keys.
        Raises the matching TelegramError subclass if the message is not found.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/getMessage",
                json={
                    "chat_id": channel_id,
                    "message_id": int(message_id),
                },
            )
            if resp.status_code != 200:
                raise_for_status(resp, "get_file_metadata")
            result = resp.json().get("result", {})
            doc = result.get("document")
            if doc:
                return {
                    "file_id": doc.get("file_id", ""),
                    "file_size": doc.get("file_size", 0),
                    "file_name": doc.get("file_name", ""),
                }
            return {}

    async def _check_rate_limit(self, channel_id: str) -> None:
        """Enforce per-channel (15/min) and global (15/sec) rate limits.

        Sleeps if the global limit is hit. Raises TelegramRateLimitError
        if the per-channel limit is exceeded.
        """
        now = asyncio.get_event_loop().time()

        self._per_channel_counts.setdefault(channel_id, [])
        self._per_channel_counts[channel_id] = [
            t
            for t in self._per_channel_counts[channel_id]
            if now - t < 60
        ]
        if len(self._per_channel_counts[channel_id]) >= 15:
            raise TelegramRateLimitError(
                "Per-channel rate limit", retry_after=30
            )
        self._per_channel_counts[channel_id].append(now)

        self._global_counts = [
            t for t in self._global_counts if now - t < 1
        ]
        if len(self._global_counts) >= 15:
            wait_time = 1 - (now - self._global_counts[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        self._global_counts.append(asyncio.get_event_loop().time())

    async def is_healthy(self) -> tuple[bool, str]:
        """Check if the bot token is valid by calling getMe.

        Returns (healthy, detail); detail is "invalid_token" on 401 so operators
        can distinguish a revoked token from a network problem.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/getMe")
                if resp.status_code == 200:
                    return True, "ok"
                if resp.status_code == 401:
                    return False, "invalid_token"
                return False, f"http_{resp.status_code}"
        except Exception:
            return False, "network_error"

    async def send_message(self, chat_id: str, text: str) -> str:
        """Send a text message to a chat. Returns message_id."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            if resp.status_code == 429:
                retry_after = (
                    resp.json().get("parameters", {}).get("retry_after", 30)
                )
                raise TelegramRateLimitError(
                    "Rate limited", retry_after=retry_after
                )
            if resp.status_code != 200:
                raise_for_status(resp, "send_message")
            return str(resp.json()["result"]["message_id"])
