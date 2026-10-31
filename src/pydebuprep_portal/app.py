"""The FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pydebuprep_portal import __version__, api
from pydebuprep_portal.db import make_engine, make_sessionmaker
from pydebuprep_portal.security import SecurityHeaders
from pydebuprep_portal.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="pydebuprep portal", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(engine)
    app.include_router(api.router)
    app.add_middleware(SecurityHeaders)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
