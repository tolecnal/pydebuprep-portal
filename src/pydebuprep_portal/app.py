"""The FastAPI application."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from pydebuprep_portal import __version__, api, auth, pages
from pydebuprep_portal.db import make_engine, make_sessionmaker
from pydebuprep_portal.security import SecurityHeaders
from pydebuprep_portal.settings import Settings, load_settings

STATIC = Path(__file__).parent / "static"
SESSION_SECONDS = 8 * 3600


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="pydebuprep portal",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # served by pages.docs from the portal's own files
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.oauth = auth.make_oauth(settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="portal_session",
        max_age=SESSION_SECONDS,
        same_site="lax",
        https_only=not settings.allow_http,
    )
    app.add_exception_handler(auth.SignInRequired, auth.sign_in_redirect)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(api.router)
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.add_middleware(SecurityHeaders)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def from_environment() -> FastAPI:
    """The app configured from PORTAL_* variables (uvicorn --factory)."""
    return create_app(load_settings(os.environ))
