"""Shared fixtures."""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from alembic import command
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from oidc_stub import OIDCStub
from pydebuprep_portal.app import create_app
from pydebuprep_portal.db import make_engine, make_sessionmaker, migrations_config
from pydebuprep_portal.ingest import store_report
from pydebuprep_portal.models import Report
from pydebuprep_portal.settings import Settings, load_settings
from pydebuprep_portal.tokens import create_token
from sample import sample_json

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


@pytest.fixture
def db_env(db: Session, db_url: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The environment of a portal using the test database."""
    env = BASE_ENV | {"PORTAL_DATABASE_URL": db_url}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env


@pytest.fixture
def make_client(db: Session, db_url: str) -> Iterator[Callable[..., TestClient]]:
    """A test client of a portal on the test database, with extra PORTAL_* settings."""
    clients: list[TestClient] = []

    def make(**env: str) -> TestClient:
        app = create_app(test_settings(PORTAL_DATABASE_URL=db_url, **env))
        c = TestClient(app, base_url="https://testserver")
        clients.append(c.__enter__())
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def api(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def token(db: Session) -> str:
    return create_token(db, kind="global", name="test", created_by="test", expires_at=None)[1]


@dataclass
class LivePortal:
    base_url: str


@pytest.fixture
def live_portal(db: Session, db_url: str) -> Iterator[LivePortal]:
    """The portal served by uvicorn on a free local port, over plain HTTP."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    app = create_app(
        test_settings(PORTAL_DATABASE_URL=db_url, PORTAL_BASE_URL=base, PORTAL_ALLOW_HTTP="1")
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    yield LivePortal(base)
    server.should_exit = True
    thread.join(10)


# --- sign-in ----------------------------------------------------------------------


@pytest.fixture
def stub() -> Iterator[OIDCStub]:
    s = OIDCStub()
    yield s
    s.close()


@pytest.fixture
def oidc_client(make_client: Callable[..., TestClient], stub: OIDCStub) -> TestClient:
    """A portal whose OIDC provider is the stub."""
    return make_client(PORTAL_OIDC_ISSUER=stub.issuer)


def login(client: TestClient, stub: OIDCStub, next_path: str = "/", **claims: Any) -> Response:
    """Sign in through the stub: /login, the provider (simulated), /auth/callback."""
    resp = client.get("/login", params={"next": next_path}, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.text
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    code = stub.code_for(query["nonce"][0], **claims)
    return client.get(
        "/auth/callback", params={"code": code, "state": query["state"][0]}, follow_redirects=False
    )


def csrf_of(client: TestClient) -> str:
    """The CSRF token from a page's logout form."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/").text)
    assert match, "no CSRF token on the page"
    return match.group(1)


# --- stored reports and people ------------------------------------------------------


@pytest.fixture
def add_report(db: Session) -> Callable[..., Report]:
    """Store a sample report (keyword arguments as for sample.sample_json)."""
    stored_token = create_token(db, kind="global", name="fixture", created_by="test",
                                expires_at=None)[0]  # fmt: skip
    received = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)

    def add(**kw: Any) -> Report:
        nonlocal received
        received += timedelta(minutes=1)
        return store_report(db, sample_json(**kw).encode(), stored_token.id, received)[0]

    return add


@pytest.fixture
def signed_in(make_client: Callable[..., TestClient], stub: OIDCStub) -> Callable[..., TestClient]:
    """A client signed in with the given groups."""

    def sign_in(*groups: str) -> TestClient:
        client = make_client(PORTAL_OIDC_ISSUER=stub.issuer)
        assert login(client, stub, groups=list(groups)).status_code == 303
        return client

    return sign_in


@pytest.fixture
def admin(signed_in: Callable[..., TestClient]) -> TestClient:
    return signed_in("pydebuprep-admins")


@pytest.fixture
def bearer(stub: OIDCStub) -> Callable[..., dict[str, str]]:
    """Authorization headers with an OIDC access token for the given groups."""

    def headers(*groups: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {stub.access_token(groups=list(groups))}"}

    return headers
