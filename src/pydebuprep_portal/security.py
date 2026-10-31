"""Security headers on every response."""

from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "object-src 'none'"
)


class SecurityHeaders(BaseHTTPMiddleware):
    """PAGE_CSP unless the route set its own, plus nosniff and a same-origin referrer."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", PAGE_CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response


CSRF_FIELD = "csrf_token"


def csrf_token(request: Request) -> str:
    """This session's CSRF token, for the forms on a page."""
    token = request.session.get("csrf")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def require_csrf(request: Request) -> None:
    """Dependency for every form POST: the form's token must match the session's."""
    sent = (await request.form()).get(CSRF_FIELD)
    expected = request.session.get("csrf")
    if not (isinstance(sent, str) and isinstance(expected, str)) or not hmac.compare_digest(
        sent, expected
    ):
        raise HTTPException(403, "the form expired; reload the page and try again")
