from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_storage_chat_id: str = ""
    telegram_log_chat_id: str = ""

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/paradox_registry"
    redis_url: str = "redis://localhost:6379/0"
    api_key_salt: str = "change-me-in-production"
    jwt_secret: str = "change-me-in-production"
    # Paradox validates Nexuss project tokens over HTTPS. These are public
    # routing values, not Nexuss admin credentials or provider secrets.
    nexuss_auth_url: str = ""
    nexuss_auth_project_id: str = ""
    # Base64 Fernet key for encrypting canonical database_url metadata.
    # If empty, the implementation derives a stable key from JWT_SECRET.
    database_url_encryption_key: str = ""
    max_upload_size_mb: int = 50
    rate_limit_uploads_per_minute: int = 15
    lock_timeout_seconds: int = 30
    telegram_rate_limit_halt: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
