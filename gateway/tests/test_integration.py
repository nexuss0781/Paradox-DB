import asyncio
import subprocess
import time
from unittest.mock import patch

import httpx
import pytest


COMPOSE_DIR = "/home/nexuss0781/Desktop/Nex/Paradox-DB"


@pytest.fixture(scope="session")
def docker_compose_up():
    """Start the Docker Compose stack and yield, then tear down."""
    subprocess.run(
        ["docker", "compose", "up", "-d", "--build", "--wait"],
        cwd=COMPOSE_DIR,
        check=True,
        timeout=120,
    )
    time.sleep(5)
    yield
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        cwd=COMPOSE_DIR,
        check=True,
        timeout=60,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_compose_all_services_running(docker_compose_up):
    """3.4.1 docker compose up starts all 4 services"""
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        cwd=COMPOSE_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    lines = [l for l in result.stdout.strip().split("\n") if l]
    assert len(lines) >= 4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_after_compose_up(docker_compose_up):
    """3.4.2 GET /health succeeds after compose up"""
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=10) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_readiness_after_compose_up(docker_compose_up):
    """3.4.3 GET /health/ready succeeds after compose up"""
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=10) as client:
        resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.integration
def test_migrations_on_fresh_postgres(docker_compose_up):
    """3.4.4 Migrations run on fresh PostgreSQL"""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "postgres", "-d", "paradox_registry",
         "-c", "\\dt"],
        cwd=COMPOSE_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "user_channels" in result.stdout
    assert "database_versions" in result.stdout
    assert "sync_log" in result.stdout


@pytest.mark.integration
def test_compose_down_cleans_up(docker_compose_up):
    """3.4.5 docker compose down cleans up"""
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        cwd=COMPOSE_DIR,
        check=True,
        timeout=60,
    )
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        cwd=COMPOSE_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    lines = [l for l in result.stdout.strip().split("\n") if l]
    running = [l for l in lines if '"State":"running"' in l or '"State":"Up"' in l]
    assert len(running) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_connection_pool_under_load(docker_compose_up):
    """3.4.6 Redis connection pool works under load"""
    import redis.asyncio as aioredis

    pool = aioredis.ConnectionPool.from_url("redis://localhost:6379/0", max_connections=50)

    async def ping_one(i: int) -> bool:
        r = aioredis.Redis(connection_pool=pool)
        try:
            result = await r.ping()
            return result
        finally:
            await r.aclose()

    results = await asyncio.gather(*[ping_one(i) for i in range(100)])
    assert all(results)
    await pool.disconnect()
