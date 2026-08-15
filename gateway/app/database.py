from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.database_url import prepare_async_database_url


def create_database_engine(database_url: str) -> AsyncEngine:
    db_url, ssl_kwargs = prepare_async_database_url(database_url)
    return create_async_engine(
        db_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        connect_args=ssl_kwargs if ssl_kwargs else {},
    )


engine = create_database_engine(settings.database_url)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(64)")
        )
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_api_key_hash ON users (api_key_hash)")
        )
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                key_hash VARCHAR(64) NOT NULL UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT now(),
                last_used_at TIMESTAMP NULL,
                expires_at TIMESTAMP NULL,
                revoked_at TIMESTAMP NULL
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys (user_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_active_lookup ON api_keys (key_hash, revoked_at, expires_at)"))


async def close_db() -> None:
    await engine.dispose()
