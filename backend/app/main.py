from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api import session as session_api
from app.config import Settings, load_settings


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
    app.state.registry = registry
    app.include_router(session_api.router)
    return app


# NOTE: no module-level `app = create_app()` here. `_build_gemini` imports
# `app.gemini.real.RealGeminiClient`, which does not exist until Task 34.
# A module-level call would execute at import time, so importing `app.main`
# from conftest.py would raise ModuleNotFoundError and block every task from
# here to 33. Task 34 adds the module-level instance once RealGeminiClient
# exists; until then, start.sh cannot run the server, which is expected since
# no task before 35 runs it.
