"""Shared pytest fixtures for work_order_service."""

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env.test", override=False)

_lifespan = importlib.import_module("apps.work_order_service.app.lifespan")
lifespan_module = _lifespan

_main = importlib.import_module("apps.work_order_service.app.main")
app = _main.app


@pytest.fixture(scope="session")
def event_loop():
    """Event loop."""
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def client():
    """Async HTTP client with lifespan workers mocked."""
    with (
        patch.object(lifespan_module, "run_scheduler_loop", new=AsyncMock()),
        patch(
            "apps.work_order_service.app.services.webhook_worker.start_webhook_worker",
            new=AsyncMock(),
        ),
        patch(
            "apps.work_order_service.app.services.webhook_worker.stop_webhook_worker",
            new=AsyncMock(),
        ),
        patch(
            "libs.shared_db.drivers.asyncpg_client.get_pool",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "libs.shared_db.drivers.asyncpg_client.close_pool",
            new=AsyncMock(),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
