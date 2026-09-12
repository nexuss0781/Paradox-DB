"""Trusted Nexuss Auth verification for Paradox gateway credentials.

The gateway never accepts a Nexuss credential as a local password. It verifies an
``nxa_`` token against the configured Nexuss project, maps that verified identity
to a Paradox user, and may mint a normal Paradox ``pk_`` key for CLI/SDK storage.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings

if TYPE_CHECKING:
    from .models import User


@dataclass(frozen=True)
class NexussIdentity:
    user_id: str
    email: str | None
    name: str | None


def parse_nexuss_identity(payload: object) -> NexussIdentity:
    """Validate the non-secret identity payload returned by Nexuss Auth."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Invalid Nexuss Auth identity response")
    user = payload.get("user")
    if (
        not isinstance(user, dict)
        or not isinstance(user.get("id"), str)
        or not user["id"].strip()
    ):
        raise HTTPException(status_code=401, detail="Nexuss Auth credential is not signed in")
    email = user.get("email")
    name = user.get("name")
    return NexussIdentity(
        user_id=user["id"].strip(),
        email=email.strip().lower() if isinstance(email, str) and email.strip() else None,
        name=name.strip() if isinstance(name, str) and name.strip() else None,
    )


def _nexuss_config() -> tuple[str, str]:
    auth_url = settings.nexuss_auth_url.strip().rstrip("/")
    project_id = settings.nexuss_auth_project_id.strip()
    if not auth_url or not project_id:
        raise HTTPException(
            status_code=503,
            detail="Nexuss Auth integration is not configured for this Paradox gateway",
        )
    return auth_url, project_id


async def verify_nexuss_api_key(api_key: str) -> NexussIdentity:
    """Verify a project-scoped Nexuss token without logging or persisting it."""
    if not api_key.startswith("nxa_"):
        raise HTTPException(status_code=401, detail="Expected a Nexuss Auth API key")
    auth_url, project_id = _nexuss_config()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(
                f"{auth_url}/v1/me",
                params={"project_id": project_id},
                headers={
                    "authorization": f"Bearer {api_key}",
                    "x-nex-auth-project": project_id,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Nexuss Auth is temporarily unavailable",
        ) from exc
    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked Nexuss Auth API key",
        )
    if response.status_code >= 500:
        raise HTTPException(status_code=503, detail="Nexuss Auth is temporarily unavailable")
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Nexuss Auth identity verification failed",
        )
    try:
        return parse_nexuss_identity(response.json())
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Invalid Nexuss Auth identity response",
        ) from exc


async def exchange_nexuss_handoff(handoff_token: str) -> NexussIdentity:
    """Exchange a one-time Nexuss handoff from a trusted Paradox web callback."""
    if not handoff_token:
        raise HTTPException(status_code=400, detail="handoff_token is required")
    auth_url, project_id = _nexuss_config()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                f"{auth_url}/v1/handoff/exchange",
                json={"projectId": project_id, "handoffToken": handoff_token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Nexuss Auth is temporarily unavailable",
        ) from exc
    if response.status_code in (400, 401, 403):
        raise HTTPException(
            status_code=401,
            detail="Invalid, expired, or replayed Nexuss handoff",
        )
    if response.status_code >= 500:
        raise HTTPException(status_code=503, detail="Nexuss Auth is temporarily unavailable")
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Nexuss Auth handoff verification failed",
        )
    try:
        payload = response.json()
        return parse_nexuss_identity(
            {"user": payload.get("user") if isinstance(payload, dict) else None}
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Invalid Nexuss Auth handoff response",
        ) from exc


def _external_username(identity: NexussIdentity) -> str:
    stem = re.sub(
        r"[^a-z0-9]+", "-", (identity.name or "nexuss-user").lower()
    ).strip("-")
    stem = stem or "nexuss-user"
    return f"{stem[:80]}-{identity.user_id.replace('-', '')[-12:]}"


async def provision_nexuss_user(identity: NexussIdentity, db: AsyncSession) -> "User":
    """Create or resolve an isolated Paradox user for a verified Nexuss identity."""
    from .models import User

    result = await db.execute(select(User).where(User.nexuss_user_id == identity.user_id))
    existing = result.scalar_one_or_none()
    if existing:
        if not existing.is_active:
            raise HTTPException(status_code=403, detail="Paradox account disabled")
        return existing

    email = identity.email or f"nexuss-{identity.user_id}@users.nexuss.invalid"
    result = await db.execute(select(User).where(User.email == email))
    email_owner = result.scalar_one_or_none()
    if email_owner:
        # Do not silently attach an external identity to a password-era account.
        raise HTTPException(
            status_code=409,
            detail=(
                "This email belongs to an existing Paradox account and must be linked explicitly"
            ),
        )

    user = User(
        email=email,
        username=_external_username(identity),
        password_hash=None,
        nexuss_user_id=identity.user_id,
    )
    db.add(user)
    await db.flush()
    return user
