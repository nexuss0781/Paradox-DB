import pytest
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql.asyncpg import dialect
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database_url import prepare_async_database_url


def test_wasmer_postgres_db_vars_build_tls_url_and_hide_credentials():
    secret_password = "pass:word@with/a?query#fragment%"
    settings = Settings(
        _env_file=None,
        database_url="",
        db_host="psql.fr-roub1.example",
        db_port="15432",
        db_name="paradox_db",
        db_username="db user",
        db_password=secret_password,
    )

    url = make_url(settings.database_url)
    assert url.drivername == "postgresql+asyncpg"
    assert url.username == "db user"
    assert url.password == secret_password
    assert url.port == 15432
    assert url.database == "paradox_db"
    assert url.query["ssl"] == "require"
    assert "sslmode" not in url.query
    prepared_url, connect_args = prepare_async_database_url(settings.database_url)
    asyncpg_args = dialect().create_connect_args(make_url(prepared_url))[1]
    assert connect_args == {}
    assert asyncpg_args["ssl"] == "require"
    assert secret_password not in repr(settings)
    assert "db_password" not in settings.model_dump()
    assert settings.database_url not in repr(settings)


def test_explicit_database_url_takes_precedence_over_wasmer_db_vars():
    explicit = "postgresql+asyncpg://app:pw@custom.example/db?ssl=require"
    settings = Settings(
        _env_file=None,
        database_url=explicit,
        db_host="psql.fr-roub1.example",
        db_port="15432",
        db_name="other_db",
        db_username="other_user",
        db_password="other_password",
    )
    assert settings.database_url == explicit


def test_database_url_preparation_translates_sslmode_for_asyncpg():
    prepared, connect_args = prepare_async_database_url(
        "postgresql+asyncpg://app:pw@custom.example/db?sslmode=require"
    )
    assert "sslmode" not in make_url(prepared).query
    assert connect_args["ssl"] == "require"


def test_local_database_default_remains_available_without_wasmer_db_vars():
    settings = Settings(_env_file=None, database_url="")
    assert (
        settings.database_url
        == "postgresql+asyncpg://postgres:postgres@localhost:5432/paradox_registry"
    )


def test_legacy_redis_env_is_ignored(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://legacy.invalid:6379/0")
    settings = Settings(_env_file=None, database_url="")
    assert (
        settings.database_url
        == "postgresql+asyncpg://postgres:postgres@localhost:5432/paradox_registry"
    )
    assert "redis_url" not in Settings.model_fields


def test_partial_wasmer_database_config_names_missing_variables_without_values():
    secret_password = "must-not-appear"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            database_url="",
            db_host="psql.fr-roub1.example",
            db_password=secret_password,
        )

    message = str(exc_info.value)
    assert "DB_PORT" in message
    assert "DB_NAME" in message
    assert "DB_USERNAME" in message
    assert secret_password not in message


def test_non_postgres_wasmer_host_is_not_guessed_as_postgres():
    with pytest.raises(ValidationError, match="Cannot safely infer PostgreSQL"):
        Settings(
            _env_file=None,
            database_url="",
            db_host="db.fr-roub1.example",
            db_port="15432",
            db_name="paradox_db",
            db_username="db_user",
            db_password="password-value",
        )
