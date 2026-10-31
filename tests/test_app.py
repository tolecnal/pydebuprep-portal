"""The app answers health checks and sends security headers."""

from __future__ import annotations

from fastapi import Response
from fastapi.testclient import TestClient

from conftest import test_settings
from pydebuprep_portal.app import create_app
from pydebuprep_portal.security import PAGE_CSP


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_pages_send_security_headers(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.headers["Content-Security-Policy"] == PAGE_CSP
    for part in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                 "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'"):  # fmt: skip
        assert part in PAGE_CSP
    assert "unsafe-inline" not in PAGE_CSP
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "same-origin"


def test_a_route_may_set_its_own_csp() -> None:
    app = create_app(test_settings())

    @app.get("/own")
    def own() -> Response:
        return Response("x", headers={"Content-Security-Policy": "default-src 'none'"})

    with TestClient(app) as c:
        resp = c.get("/own")
    assert resp.headers["Content-Security-Policy"] == "default-src 'none'"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
