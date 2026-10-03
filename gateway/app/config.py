from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL

_LOCAL_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/paradox_registry"


class Settings(BaseSettings):
    telegram_bot_token: str = Field(default="", repr=False, exclude=True)
    telegram_api_id: str = Field(default="", repr=False, exclude=True)
    telegram_api_hash: str = Field(default="", repr=False, exclude=True)
    telegram_storage_chat_id: str = Field(default="", repr=False, exclude=True)
    telegram_log_chat_id: str = Field(default="", repr=False, exclude=True)

    database_url: str = Field(default="", repr=False, exclude=True)
    db_host: str = Field(default="", repr=False, exclude=True)
    db_port: str = Field(default="", repr=False, exclude=True)
    db_name: str = Field(default="", repr=False, exclude=True)
    db_username: str = Field(default="", repr=False, exclude=True)
    db_password: str = Field(default="", repr=False, exclude=True)

    api_key_salt: str = Field(default="change-me-in-production", repr=False, exclude=True)
    jwt_secret: str = Field(default="change-me-in-production", repr=False, exclude=True)
    # Paradox validates Nexuss project tokens over HTTPS. These are public
    # routing values, not Nexuss admin credentials or provider secrets.
    nexuss_auth_url: str = ""
    nexuss_auth_project_id: str = ""
    # Base64 Fernet key for encrypting canonical database_url metadata.
    # If empty, the implementation derives a stable key from JWT_SECRET.
    database_url_encryption_key: str = Field(default="", repr=False, exclude=True)
    max_upload_size_mb: int = 50
    rate_limit_uploads_per_minute: int = 15
    lock_timeout_seconds: int = 30
    telegram_rate_limit_halt: bool = False

    @model_validator(mode="after")
    def configure_wasmer_database_url(self) -> Settings:
        if self.database_url.strip():
            return self

        db_values = {
            "DB_HOST": self.db_host.strip(),
            "DB_PORT": self.db_port.strip(),
            "DB_NAME": self.db_name.strip(),
            "DB_USERNAME": self.db_username,
            "DB_PASSWORD": self.db_password,
        }
        present = {name: value for name, value in db_values.items() if value}
        if not present:
            self.database_url = _LOCAL_DATABASE_URL
            return self

        missing = [name for name, value in db_values.items() if not value]
        if missing:
            raise ValueError(
                "DATABASE_URL is unset and Wasmer DB_* variables are incomplete; "
                "missing: " + ", ".join(missing)
            )

        # Wasmer documents psql.* hosts for PostgreSQL and db.* hosts for
        # MySQL. Do not guess a driver if the managed host does not identify PG.
        if not self.db_host.lower().startswith("psql."):
            raise ValueError(
                "Cannot safely infer PostgreSQL from DB_HOST; configure DATABASE_URL explicitly"
            )
        try:
            port = int(self.db_port)
        except ValueError as exc:
            raise ValueError("DB_PORT must be an integer") from exc

        url = URL.create(
            "postgresql+asyncpg",
            username=self.db_username,
            password=self.db_password,
            host=self.db_host,
            port=port,
            database=self.db_name,
            query={"ssl": "require"},
        )
        self.database_url = url.render_as_string(hide_password=False)
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "hide_input_in_errors": True,
        "extra": "ignore",
    }


settings = Settings()
