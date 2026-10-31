"""The config API for people and scripts (OIDC access tokens, the same roles)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import make_team
from pydebuprep_portal.models import ConfigVersion

Bearer = Callable[..., dict[str, str]]
MEMBER = ("pydebuprep-team-web",)
BASE = "/api/v1/teams/web/configs"


@pytest.fixture
def client(make_client: Callable[..., TestClient], stub: Any, db: Session) -> TestClient:
    make_team(db, "web")
    make_team(db, "db")
    return make_client(PORTAL_OIDC_ISSUER=stub.issuer)


def test_api_create_get_put_round_trip(client: TestClient, bearer: Bearer) -> None:
    h = bearer(*MEMBER)
    resp = client.post(
        BASE, json={"name": "default", "values": {"report": {"todo": True}}}, headers=h
    )
    assert resp.status_code == 201
    assert resp.json() == {"name": "default", "version": 1,
                           "url": "https://portal.test/api/v1/configs/web/default"}  # fmt: skip
    listed = client.get(BASE, headers=h).json()["configs"]
    assert [(c["name"], c["version"], c["url"]) for c in listed] == [
        ("default", 1, resp.json()["url"])
    ]
    put = client.put(
        f"{BASE}/default",
        json={"values": {"report": {"todo": True, "min_severity": "critical"}}},
        headers=h,
    )
    assert put.json() == {"version": 2, "changed": True}
    again = client.put(
        f"{BASE}/default",
        json={"values": {"report": {"min_severity": "critical", "todo": True}}},
        headers=h,
    )
    assert again.json() == {"version": 2, "changed": False}
    detail = client.get(f"{BASE}/default", headers=h).json()
    assert (detail["team"], detail["name"], detail["version"]) == ("web", "default", 2)
    assert detail["values"] == {"report": {"min_severity": "critical", "todo": True}}
    assert [v["version"] for v in detail["versions"]] == [1, 2]
    assert detail["versions"][0]["created_by"] == "Ada Admin <user-1>"


@pytest.mark.parametrize(
    "values",
    [
        {"upload": {"url": "https://evil.example"}},
        {"report": {"output_dir": "/etc"}},
        {"report": {"snapshot_dir": "/etc"}},
        {"report": {"baseline": "/etc/shadow"}},
        {"checks": {"postgresql": {"credentials_file": "/root/.pgpass"}}},
        {"checks": {"python-venvs": {"search_paths": ["/"]}}},
        {"checks": {"docker": {"http_probes": ["x=http://127.0.0.1/"]}}},
        {"checks": {"nope": {}}},
        "not a mapping",
    ],
)
def test_api_refuses_unsafe_values(
    client: TestClient, bearer: Bearer, db: Session, values: Any
) -> None:
    h = bearer(*MEMBER)
    client.post(BASE, json={"name": "default"}, headers=h)
    resp = client.put(f"{BASE}/default", json={"values": values}, headers=h)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list) and resp.json()["detail"]
    assert client.post(BASE, json={"name": "other", "values": values}, headers=h).status_code == 422
    assert db.query(ConfigVersion).count() == 1


def test_api_roles(client: TestClient, bearer: Bearer, token: str) -> None:
    client.post(BASE, json={"name": "default"}, headers=bearer(*MEMBER))
    auditor, other = bearer("pydebuprep-auditors"), bearer("pydebuprep-team-db")
    assert client.get(f"{BASE}/default", headers=auditor).status_code == 200
    assert client.put(f"{BASE}/default", json={"values": {}}, headers=auditor).status_code == 403
    assert client.post(BASE, json={"name": "x"}, headers=auditor).status_code == 403
    for path in (BASE, f"{BASE}/default", f"{BASE}/default/versions/1"):
        assert client.get(path, headers=other).status_code == 404, path
    assert client.put(f"{BASE}/default", json={"values": {}}, headers=other).status_code == 404
    upload = {"Authorization": f"Bearer {token}"}
    assert client.get(BASE, headers=upload).status_code == 401
    assert client.get(f"{BASE}/nope", headers=bearer(*MEMBER)).status_code == 404


def test_api_version_yaml(client: TestClient, bearer: Bearer) -> None:
    h = bearer(*MEMBER)
    client.post(BASE, json={"name": "default"}, headers=h)
    client.put(f"{BASE}/default", json={"values": {"report": {"todo": True}}}, headers=h)
    first = client.get(f"{BASE}/default/versions/1", headers=h)
    assert first.headers["Content-Type"] == "application/yaml; charset=utf-8"
    assert yaml.safe_load(first.text) == {"upload": {"url": "https://portal.test"}}
    assert yaml.safe_load(client.get(f"{BASE}/default/versions/2", headers=h).text)["report"] == {
        "todo": True
    }
    assert client.get(f"{BASE}/default/versions/9", headers=h).status_code == 404
