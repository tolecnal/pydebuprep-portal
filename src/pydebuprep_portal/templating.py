"""Jinja2 pages: every page knows who is looking and the session's CSRF token."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.security import CSRF_FIELD, csrf_token

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _urlencode_set(params: dict[str, Any]) -> str:
    """Query string of the parameters that are set."""
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})


templates.env.filters["utc"] = _utc
templates.env.filters["urlencode_set"] = _urlencode_set


def page(
    request: Request, name: str, viewer: Viewer | None, status_code: int = 200, **context: Any
) -> HTMLResponse:
    context |= {"viewer": viewer, "csrf_field": CSRF_FIELD}
    if viewer is not None:
        context["csrf_token"] = csrf_token(request)
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def error_page(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
    return page(request, "error.html", None, status_code, title=title, message=message)
