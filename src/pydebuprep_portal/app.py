"""The FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from starlette.middleware.sessions import SessionMiddleware

from pydebuprep_portal import __version__, api, auth, pages
from pydebuprep_portal.db import make_engine, make_sessionmaker
from pydebuprep_portal.retention import run_expiry_forever
from pydebuprep_portal.security import SecurityHeaders
from pydebuprep_portal.settings import Settings, load_settings

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings, engine: Engine | None = None) -> FastAPI:
    own_engine = engine is None
    db_engine = engine if engine is not None else make_engine(settings.database_url)

    maker = make_sessionmaker(db_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        expiry = asyncio.create_task(run_expiry_forever(maker))
        yield
        expiry.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await expiry
        if own_engine:
            db_engine.dispose()

    app = FastAPI(
        title="pydebuprep portal",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # served by pages.docs from the portal's own files
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.sessionmaker = maker
    app.state.oauth = auth.make_oauth(settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="portal_session",
        max_age=auth.SESSION_SECONDS,
        same_site="lax",
        https_only=not settings.allow_http,
    )
    app.add_exception_handler(auth.SignInRequired, auth.sign_in_redirect)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(api.router)
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.add_middleware(SecurityHeaders, hsts=settings.base_url.startswith("https://"))

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def from_environment() -> FastAPI:
    """The app configured from PORTAL_* variables (uvicorn --factory)."""
    if "PORTAL_MAX_UPLOAD_BYTES" in os.environ:
        logging.getLogger(__name__).warning(
            "PORTAL_MAX_UPLOAD_BYTES is no longer read: set the upload limit on the Settings page"
        )
    return create_app(load_settings(os.environ))
