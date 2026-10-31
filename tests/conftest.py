"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pydebuprep_portal.app import create_app
from pydebuprep_portal.settings import Settings, load_settings

SECRET = "s" * 32

BASE_ENV = {
    "PORTAL_DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/db",
    "PORTAL_BASE_URL": "https://portal.test",
    "PORTAL_SESSION_SECRET": SECRET,
    "PORTAL_OIDC_ISSUER": "https://idp.test/realms/r",
    "PORTAL_OIDC_CLIENT_ID": "portal",
    "PORTAL_OIDC_CLIENT_SECRET": "client-secret",
}


def test_settings(**env: str) -> Settings:
    return load_settings(BASE_ENV | env)


test_settings.__test__ = False  # type: ignore[attr-defined]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(test_settings())) as c:
        yield c
