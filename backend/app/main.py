from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api import projects as projects_api
from app.api import session as session_api
from app.api import ws as ws_api
from app.config import Settings, load_settings
from app.pipeline import Deps
from app.realtime import RealtimeRegistry


def _build_gemini(settings: Settings):
    if settings.use_fake_gemini:
        from app.gemini.fake import FakeGeminiClient
        return FakeGeminiClient()
    from app.gemini.real import RealGeminiClient
    return RealGeminiClient(settings)


def create_app(*, settings: Settings | None = None, gemini=None, registry=None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as conn:
        db.init_schema(conn)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(title="Book Illustration Studio", lifespan=lifespan)
    app.state.settings = settings
    app.state.gemini = gemini if gemini is not None else _build_gemini(settings)
    app.state.registry = registry if registry is not None else RealtimeRegistry()
    app.state.deps = Deps(settings=settings, gemini=app.state.gemini,
                          registry=app.state.registry)
    app.include_router(session_api.router)
    app.include_router(projects_api.router)
    app.include_router(ws_api.router)
    return app


def __getattr__(name: str):
    """Lazy `app.main:app` for uvicorn (start.sh). A plain module-level
    create_app() would run at import time - and conftest.py imports this module
    in every test run, where there is no .env and no real key."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
