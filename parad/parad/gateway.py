"""HTTP client for the Paradox-DB Gateway REST API."""

import base64
import httpx
from parad.types import (
    RegisterResponse,
    UploadResponse,
    StatusResponse,
    VersionsResponse,
    RollbackResponse,
    DownloadResult,
)

COLD_START_TIMEOUT = httpx.Timeout(connect=60.0, read=120.0, write=120.0, pool=60.0)
SHORT_TIMEOUT = httpx.Timeout(connect=60.0, read=60.0, write=60.0, pool=60.0)


class GatewayError(Exception):
    """Gateway API error."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class GatewayClient:
    """Client for the Paradox-DB Gateway REST API."""

    def __init__(self, gateway_url: str, api_key: str = ""):
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def register(self, channel_id: str = "", bot_token_id: str = "") -> RegisterResponse:
        """Register a new user with the gateway."""
        resp = httpx.post(
            f"{self.gateway_url}/auth/register",
            json={"channel_id": channel_id, "bot_token_id": bot_token_id},
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise GatewayError(resp.status_code, resp.text)
        data = resp.json()
        self.api_key = data.get("api_key", self.api_key)
        return RegisterResponse(**data)

    def upload(
        self,
        database_name: str,
        file_bytes: bytes,
        version: int = 0,
        version_type: str = "full",
        encryption_key: str = "",
    ) -> UploadResponse:
        """Upload a database file to the gateway."""
        file_b64 = base64.b64encode(file_bytes).decode()
        payload = {
            "database_name": database_name,
            "file_data": file_b64,
            "version_type": version_type,
            "version": version,
        }
        if encryption_key:
            payload["encryption_key"] = encryption_key
        resp = httpx.post(
            f"{self.gateway_url}/upload",
            json=payload,
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise GatewayError(resp.status_code, resp.text)
        return UploadResponse(**resp.json())

    def download(self, database_name: str, version: int | None = None, encryption_key: str = "") -> DownloadResult:
        """Download a database file from the gateway."""
        params = {"database_name": database_name}
        if version is not None:
            params["version"] = version
        if encryption_key:
            params["encryption_key"] = encryption_key
        resp = httpx.get(
            f"{self.gateway_url}/download",
            params=params,
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise GatewayError(resp.status_code, resp.text)
        return DownloadResult(
            bytes=resp.content,
            version=int(resp.headers.get("x-version", 0)) or None,
            message_id=resp.headers.get("x-message-id"),
        )

    def status(self) -> StatusResponse:
        """Get sync status from the gateway."""
        resp = httpx.get(
            f"{self.gateway_url}/status",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise GatewayError(resp.status_code, resp.text)
        return StatusResponse(**resp.json())

    def versions(self, database_name: str) -> VersionsResponse:
        """List all versions for a database."""
        resp = httpx.get(
            f"{self.gateway_url}/versions",
            params={"database_name": database_name},
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise GatewayError(resp.status_code, resp.text)
        return VersionsResponse(**resp.json())

    def rollback(self, database_name: str, target_version: int) -> RollbackResponse:
        """Rollback a database to a target version."""
        resp = httpx.post(
            f"{self.gateway_url}/rollback",
            json={"database_name": database_name, "target_version": target_version},
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise GatewayError(resp.status_code, resp.text)
        return RollbackResponse(**resp.json())
