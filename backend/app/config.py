from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_TEXT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    text_model: str
    image_model: str
    data_dir: Path
    db_path: Path
    use_fake_gemini: bool
    server_run_id: str
    request_timeout_seconds: float


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    return Settings(
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        text_model=os.environ.get("GEMINI_TEXT_MODEL", DEFAULT_TEXT_MODEL),
        image_model=os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        use_fake_gemini=os.environ.get("USE_FAKE_GEMINI", "0") == "1",
        server_run_id=uuid.uuid4().hex,
        request_timeout_seconds=float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "180")),
    )
