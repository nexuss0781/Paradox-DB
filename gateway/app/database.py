from collections.abc import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.database_url import prepare_async_database_url


_db_url, _ssl_kwargs = prepare_async_database_url(settings.database_url)

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    connect_args=_ssl_kwargs if _ssl_kwargs else {},
)

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


async def close_db() -> None:
    await engine.dispose()
