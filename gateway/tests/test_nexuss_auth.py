import pytest
from fastapi import HTTPException

from app.nexuss_auth import parse_nexuss_identity


def test_parse_nexuss_identity_accepts_google_profile_shape():
    identity = parse_nexuss_identity(
        {"user": {"id": "google-user-123", "email": "Person@Example.com", "name": "Person Example"}}
    )
    assert identity.user_id == "google-user-123"
    assert identity.email == "person@example.com"
    assert identity.name == "Person Example"


def test_parse_nexuss_identity_rejects_signed_out_response():
    with pytest.raises(HTTPException) as exc:
        parse_nexuss_identity({"user": None})
    assert exc.value.status_code == 401
