from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings


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
