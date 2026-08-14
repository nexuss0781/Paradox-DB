from app.database import create_database_engine


def test_engine_construction_uses_asyncpg_for_render_style_url() -> None:
    engine = create_database_engine(
        "postgresql://user:password@host.render.com:5432/paradox?sslmode=require"
    )

    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.sync_engine.url.drivername == "postgresql+asyncpg"
