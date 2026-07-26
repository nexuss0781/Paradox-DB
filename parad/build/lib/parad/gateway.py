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
            h["Authorization"] = f"Bearer {self.api_key}"
            h["X-API-Key"] = self.api_key
        return h

    def _check(self, resp: httpx.Response):
        """Raise GatewayError on non-2xx responses."""
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise GatewayError(resp.status_code, detail)

    # ── Legacy auth ──────────────────────────────────────────────

    def register(self, channel_id: str = "", bot_token_id: str = "") -> RegisterResponse:
        """Register a new user with the gateway (legacy Telegram flow)."""
        resp = httpx.post(
            f"{self.gateway_url}/auth/register",
            json={"channel_id": channel_id, "bot_token_id": bot_token_id},
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        self.api_key = data.get("api_key", self.api_key)
        return RegisterResponse(**data)

    # ── Email auth ───────────────────────────────────────────────

    def register_email(self, email: str, username: str, password: str) -> dict:
        """Register a new account with email, username, and password."""
        resp = httpx.post(
            f"{self.gateway_url}/auth/register",
            json={"email": email, "username": username, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        token = data.get("access_token") or data.get("api_key", "")
        if token:
            self.api_key = token
        return data

    def login(self, email: str, password: str) -> dict:
        """Login with email and password."""
        resp = httpx.post(
            f"{self.gateway_url}/auth/login",
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        token = data.get("access_token") or data.get("api_key", "")
        if token:
            self.api_key = token
        return data

    def get_me(self) -> dict:
        """Get the current authenticated user's profile."""
        resp = httpx.get(
            f"{self.gateway_url}/auth/me",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    # ── Projects ─────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        """List all projects for the current user."""
        resp = httpx.get(
            f"{self.gateway_url}/projects",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        return data if isinstance(data, list) else data.get("projects", [])

    def create_project(self, name: str, description: str = "") -> dict:
        """Create a new project."""
        payload: dict = {"name": name}
        if description:
            payload["description"] = description
        resp = httpx.post(
            f"{self.gateway_url}/projects",
            json=payload,
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    def get_project(self, project_id: str) -> dict:
        """Get a single project by ID."""
        resp = httpx.get(
            f"{self.gateway_url}/projects/{project_id}",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    def delete_project(self, project_id: str) -> dict:
        """Delete a project and all its databases."""
        resp = httpx.delete(
            f"{self.gateway_url}/projects/{project_id}",
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json() if resp.text else {}

    # ── Databases ────────────────────────────────────────────────

    def list_databases(self, project_id: str) -> list[dict]:
        """List all databases in a project."""
        resp = httpx.get(
            f"{self.gateway_url}/projects/{project_id}/databases",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        return data if isinstance(data, list) else data.get("databases", [])

    def create_database(self, project_id: str, name: str, description: str = "") -> dict:
        """Create a new database in a project."""
        payload: dict = {"name": name}
        if description:
            payload["description"] = description
        resp = httpx.post(
            f"{self.gateway_url}/projects/{project_id}/databases",
            json=payload,
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    def get_database(self, database_id: str) -> dict:
        """Get database details by ID."""
        resp = httpx.get(
            f"{self.gateway_url}/databases/{database_id}",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    def delete_database(self, database_id: str) -> dict:
        """Delete a database and all its versions."""
        resp = httpx.delete(
            f"{self.gateway_url}/databases/{database_id}",
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json() if resp.text else {}

    # ── Versions ─────────────────────────────────────────────────

    def list_versions(self, database_id: str) -> list[dict]:
        """List all versions for a database."""
        resp = httpx.get(
            f"{self.gateway_url}/databases/{database_id}/versions",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        return data if isinstance(data, list) else data.get("versions", [])

    def diff_versions(self, database_id: str, version_a: int, version_b: int) -> dict:
        """Compare two versions of a database."""
        resp = httpx.get(
            f"{self.gateway_url}/databases/{database_id}/diff",
            params={"version_a": version_a, "version_b": version_b},
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    # ── Backups ──────────────────────────────────────────────────

    def create_backup(self, database_id: str, name: str, notes: str = "") -> dict:
        """Create a named backup at the current version."""
        payload: dict = {"name": name}
        if notes:
            payload["notes"] = notes
        resp = httpx.post(
            f"{self.gateway_url}/databases/{database_id}/backups",
            json=payload,
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    def list_backups(self, database_id: str) -> list[dict]:
        """List all backups for a database."""
        resp = httpx.get(
            f"{self.gateway_url}/databases/{database_id}/backups",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        data = resp.json()
        return data if isinstance(data, list) else data.get("backups", [])

    def restore_backup(self, database_id: str, backup_id: str) -> dict:
        """Restore a database from a backup."""
        resp = httpx.post(
            f"{self.gateway_url}/databases/{database_id}/backups/{backup_id}/restore",
            headers=self._headers(),
            timeout=COLD_START_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
        return resp.json()

    # ── Legacy sync endpoints ────────────────────────────────────

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
        self._check(resp)
        return UploadResponse(**resp.json())

    def download(self, database_name: str, version: int | None = None, encryption_key: str = "") -> DownloadResult:
        """Download a database file from the gateway."""
        params: dict = {"database_name": database_name}
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
        self._check(resp)
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
        self._check(resp)
        return StatusResponse(**resp.json())

    def versions(self, database_name: str) -> VersionsResponse:
        """List all versions for a database (legacy by name)."""
        resp = httpx.get(
            f"{self.gateway_url}/versions",
            params={"database_name": database_name},
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            follow_redirects=True,
        )
        self._check(resp)
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
        self._check(resp)
        return RollbackResponse(**resp.json())
