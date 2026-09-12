import pytest

from app.database_url import prepare_async_database_url


def test_render_postgresql_url_is_normalized_to_asyncpg() -> None:
    url, connect_args = prepare_async_database_url(
        "postgresql://user:password@host.render.com:5432/paradox?sslmode=require"
    )

    assert url == "postgresql+asyncpg://user:password@host.render.com:5432/paradox"
    assert connect_args == {"ssl": "require"}


def test_existing_asyncpg_url_is_preserved() -> None:
    url, connect_args = prepare_async_database_url(
        "postgresql+asyncpg://user:password@localhost:5432/paradox"
    )

    assert url == "postgresql+asyncpg://user:password@localhost:5432/paradox"
    assert connect_args == {}


def test_unsupported_driver_is_rejected() -> None:
    with pytest.raises(ValueError, match="supported by asyncpg"):
        prepare_async_database_url("mysql://user:password@localhost:3306/paradox")
