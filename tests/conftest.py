"""Shared fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from pydebuprep_portal.app import create_app
from pydebuprep_portal.db import make_engine, make_sessionmaker, migrations_config
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


# --- database ---------------------------------------------------------------------

TABLES = ("hosts", "tokens", "reports", "findings")


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("PORTAL_TEST_DATABASE_URL", "")
    if not url:
        pytest.fail("the database tests need PostgreSQL: run `make db-up` and `make test`")
    return url


@pytest.fixture(scope="session")
def engine(db_url: str) -> Iterator[Engine]:
    eng = make_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(migrations_config(db_url), "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    with make_sessionmaker(engine)() as session:
        yield session
