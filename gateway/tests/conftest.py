import asyncio
import sys

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for all async tests."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
