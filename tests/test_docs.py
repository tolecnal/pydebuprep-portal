"""The API documentation is served from the portal itself."""

from __future__ import annotations

from fastapi.testclient import TestClient

from pydebuprep_portal.security import DOCS_CSP


def test_swagger_ui_is_served_locally(client: TestClient) -> None:
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "/static/swagger/swagger-ui-bundle.js" in resp.text
    assert "/static/swagger-init.js" in resp.text
    assert "cdn.jsdelivr" not in resp.text and "unpkg" not in resp.text
    assert resp.headers["Content-Security-Policy"] == DOCS_CSP
    assert "script-src 'self';" in DOCS_CSP


def test_swagger_assets_and_schema(client: TestClient) -> None:
    for path in ("/static/swagger/swagger-ui-bundle.js", "/static/swagger/swagger-ui.css",
                 "/static/swagger-init.js"):  # fmt: skip
        assert client.get(path).status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/v1/reports", "/api/v1/reports/{report_id}", "/api/v1/whoami"} <= set(paths)
    assert "/" not in paths and "/login" not in paths


def test_redoc_is_off(client: TestClient) -> None:
    assert client.get("/redoc").status_code == 404
