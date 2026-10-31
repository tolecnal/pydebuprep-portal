"""Hosts fetch their config with the token they upload with."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import test_settings
from oidc_stub import OIDCStub
from pydebuprep_portal.access import Viewer
from pydebuprep_portal.configs import save_config
from pydebuprep_portal.models import Token

URL = "/api/v1/configs/web/default"


def get(client: TestClient, token: str | None, url: str = URL, **headers: str):  # type: ignore[no-untyped-def]
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.get(url, headers=headers)


@pytest.mark.parametrize(
    ("token", "status"),
    [
        ("global", 200),
        ("web-team", 200),
        ("web1-host", 200),
        ("db-team", 404),
        ("revoked", 401),
        ("expired", 401),
        (None, 401),
    ],
)
def test_who_may_fetch(
    api: TestClient, fetch_tokens: dict[str, str], token: str | None, status: int
) -> None:
    resp = get(api, None if token is None else fetch_tokens[token])
    assert resp.status_code == status
    if status == 401:
        assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_oidc_access_token_is_not_an_upload_token(
    make_client,
    stub: OIDCStub,
    fetch_tokens: dict[str, str],  # type: ignore[no-untyped-def]
) -> None:
    client = make_client(PORTAL_OIDC_ISSUER=stub.issuer)
    assert get(client, stub.access_token(groups=["pydebuprep-admins"])).status_code == 401


def test_unknown_config_and_forbidden_config_look_the_same(
    api: TestClient, fetch_tokens: dict[str, str]
) -> None:
    forbidden = get(api, fetch_tokens["db-team"])
    unknown = get(api, fetch_tokens["global"], "/api/v1/configs/web/nope")
    no_team = get(api, fetch_tokens["global"], "/api/v1/configs/nope/default")
    assert forbidden.status_code == unknown.status_code == no_team.status_code == 404
    assert forbidden.json() == unknown.json() == no_team.json()


def test_etag_and_304(api: TestClient, fetch_tokens: dict[str, str], db: Session) -> None:
    first = get(api, fetch_tokens["global"])
    tag = first.headers["ETag"]
    again = get(api, fetch_tokens["global"], **{"If-None-Match": tag})
    assert (again.status_code, again.content, again.headers["ETag"]) == (304, b"", tag)
    member = Viewer("m", "Mia", frozenset(), teams=frozenset({"web"}))
    save_config(db, member, test_settings(), "web", "default", {"report": {"todo": False}})
    changed = get(api, fetch_tokens["global"], **{"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["ETag"] != tag


def test_served_yaml_has_upload_url_and_header(
    api: TestClient, fetch_tokens: dict[str, str]
) -> None:
    resp = get(api, fetch_tokens["web-team"])
    assert resp.headers["Content-Type"] == "application/yaml; charset=utf-8"
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.text.startswith(
        "# pydebuprep config web/default, version 2, from https://portal.test/api/v1/configs/web/default\n"
    )
    assert yaml.safe_load(resp.text) == {
        "report": {"todo": True},
        "upload": {"url": "https://portal.test"},
    }


def test_fetch_records_token_use(
    api: TestClient, fetch_tokens: dict[str, str], db: Session
) -> None:
    get(api, fetch_tokens["web1-host"])
    public_id = fetch_tokens["web1-host"].split("_")[1]
    db.expire_all()
    assert db.query(Token).filter_by(public_id=public_id).one().last_used_at is not None
