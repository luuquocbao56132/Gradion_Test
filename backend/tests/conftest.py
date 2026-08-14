from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import Settings
from app.gemini.fake import FakeGeminiClient
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        gemini_api_key="test-key",
        text_model="test-text-model",
        image_model="test-image-model",
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        use_fake_gemini=True,
        server_run_id="run-A",
        request_timeout_seconds=5.0,
    )


@pytest.fixture
def other_run(settings: Settings) -> Settings:
    """The same database seen by a different process identity."""
    return replace(settings, server_run_id="run-B")


@pytest.fixture
def fake_gemini() -> FakeGeminiClient:
    return FakeGeminiClient()


@pytest.fixture
def app(settings, fake_gemini):
    return create_app(settings=settings, gemini=fake_gemini)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def aclient(app):
    """An httpx client over the ASGI app, for tests that need real concurrency."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        # create_app's lifespan does not run under ASGITransport; init explicitly.
        yield async_client
