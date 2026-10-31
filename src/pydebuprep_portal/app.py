"""The FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI

from pydebuprep_portal import __version__
from pydebuprep_portal.security import SecurityHeaders
from pydebuprep_portal.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="pydebuprep portal", version=__version__)
    app.state.settings = settings
    app.add_middleware(SecurityHeaders)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
