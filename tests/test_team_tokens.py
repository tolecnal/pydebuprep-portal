"""Team and host tokens: made by admins and the team's lead; audited."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import csrf_of, make_team
from pydebuprep_portal.manage import main
from pydebuprep_portal.models import AuditEntry, Token

Bearer = Callable[..., dict[str, str]]
TOKEN = re.compile(r"pdp_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")
LEAD = ("pydebuprep-team-web-lead",)


def create(client: TestClient, team: str = "web", **form: str) -> object:
    return client.post(f"/teams/{team}/tokens", data={"csrf_token": csrf_of(client)} | form)


def test_lead_creates_team_and_host_tokens(
    signed_in: Callable[..., TestClient], db: Session
) -> None:
    lead = signed_in(*LEAD)
    resp = create(lead, kind="team", name="web servers")
    assert resp.status_code == 200 and TOKEN.search(resp.text)  # type: ignore[attr-defined]
    assert resp.headers["Cache-Control"] == "no-store"  # type: ignore[attr-defined]
    resp = create(lead, kind="host", name="web1 only", host_name="Web1")
    assert resp.status_code == 200  # type: ignore[attr-defined]
    tokens = {t.name: t for t in db.query(Token)}
    team = make_team(db, "web")
    assert (tokens["web servers"].kind, tokens["web servers"].team_id) == ("team", team.id)
    assert (tokens["web1 only"].kind, tokens["web1 only"].host_name) == ("host", "web1")
    actions = [(e.action, e.target) for e in db.query(AuditEntry).order_by(AuditEntry.id)
               if e.action.startswith("token.")]  # fmt: skip
    assert actions == [("token.create", "web servers"), ("token.create", "web1 only")]
    page = lead.get("/teams/web").text
    assert "web servers" in page and "web1 only" in page


def test_member_and_other_lead_cannot(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    make_team(db, "web")
    member = signed_in("pydebuprep-team-web")
    assert create(member, kind="team", name="x").status_code == 403  # type: ignore[attr-defined]
    assert 'action="/teams/web/tokens"' not in member.get("/teams/web").text
    other = signed_in("pydebuprep-team-db-lead")
    assert create(other, kind="team", name="x").status_code == 404  # type: ignore[attr-defined]  # can't see web
    resp = other.post("/api/v1/teams/web/tokens", json={"kind": "team", "name": "x"},
                      headers=bearer("pydebuprep-team-db-lead"))  # fmt: skip
    assert resp.status_code == 404
    resp = member.post("/api/v1/teams/web/tokens", json={"kind": "team", "name": "x"},
                       headers=bearer("pydebuprep-team-web"))  # fmt: skip
    assert resp.status_code == 403
    assert db.query(Token).count() == 0


def test_lead_cannot_make_a_global_token_through_the_team(
    signed_in: Callable[..., TestClient], db: Session
) -> None:
    assert create(signed_in(*LEAD), kind="global", name="x").status_code == 422  # type: ignore[attr-defined]
    assert db.query(Token).count() == 0


def test_admin_manages_any_teams_tokens(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    make_team(db, "web")
    admin = signed_in("pydebuprep-admins")
    headers = bearer("pydebuprep-admins")
    resp = admin.post("/api/v1/teams/web/tokens", json={"kind": "host", "name": "h", "host_name": "h1"},
                      headers=headers)  # fmt: skip
    assert resp.status_code == 201
    listed = admin.get("/api/v1/teams/web/tokens", headers=headers).json()["tokens"]
    assert [(t["kind"], t["team"], t["host_name"]) for t in listed] == [("host", "web", "h1")]
    everything = admin.get("/api/v1/tokens", headers=headers).json()["tokens"]
    assert everything[0]["team"] == "web"
    assert (
        admin.delete(f"/api/v1/teams/web/tokens/{resp.json()['id']}", headers=headers).status_code
        == 204
    )
    assert "web" in admin.get("/tokens").text


def test_auditor_reads_team_tokens(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    create(signed_in(*LEAD), kind="team", name="seen")
    auditor = signed_in("pydebuprep-auditors")
    assert "seen" in auditor.get("/teams/web").text
    headers = bearer("pydebuprep-auditors")
    assert [
        t["name"] for t in auditor.get("/api/v1/teams/web/tokens", headers=headers).json()["tokens"]
    ] == ["seen"]
    assert (
        auditor.post(
            "/api/v1/teams/web/tokens", json={"kind": "team", "name": "x"}, headers=headers
        ).status_code
        == 403
    )
    assert create(auditor, kind="team", name="x").status_code == 403  # type: ignore[attr-defined]


@pytest.mark.parametrize("host_name", ["", "a b", "a" * 254, None])
def test_host_token_needs_a_valid_host_name(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session, host_name: str | None
) -> None:
    lead = signed_in(*LEAD)
    form = {"kind": "host", "name": "h"} | ({} if host_name is None else {"host_name": host_name})
    assert create(lead, **form).status_code == 422  # type: ignore[attr-defined]
    body = {"kind": "host", "name": "h"} | ({} if host_name is None else {"host_name": host_name})
    assert (
        lead.post("/api/v1/teams/web/tokens", json=body, headers=bearer(*LEAD)).status_code == 422
    )
    assert db.query(Token).count() == 0


def test_team_token_with_a_host_name_is_422(
    signed_in: Callable[..., TestClient], bearer: Bearer
) -> None:
    lead = signed_in(*LEAD)
    resp = lead.post("/api/v1/teams/web/tokens", json={"kind": "team", "name": "t", "host_name": "h1"},
                     headers=bearer(*LEAD))  # fmt: skip
    assert resp.status_code == 422


def test_revoke_through_another_team_is_404(
    signed_in: Callable[..., TestClient], db: Session
) -> None:
    make_team(db, "db")
    admin = signed_in("pydebuprep-admins")
    create(admin, team="db", kind="team", name="db token")
    token = db.query(Token).one()
    lead = signed_in(*LEAD)
    resp = lead.post(f"/teams/web/tokens/{token.id}/revoke", data={"csrf_token": csrf_of(lead)})
    assert resp.status_code == 404
    db.refresh(token)
    assert token.revoked_at is None


def test_revoke_is_audited_once(signed_in: Callable[..., TestClient], db: Session) -> None:
    lead = signed_in(*LEAD)
    create(lead, kind="team", name="t")
    token = db.query(Token).one()
    for _ in range(2):
        resp = lead.post(f"/teams/web/tokens/{token.id}/revoke", data={"csrf_token": csrf_of(lead)},
                         follow_redirects=False)  # fmt: skip
        assert (resp.status_code, resp.headers["location"]) == (303, "/teams/web")
    assert db.query(AuditEntry).filter_by(action="token.revoke").count() == 1


def test_manage_create_token_is_audited(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-token", "bootstrap"]) == 0
    capsys.readouterr()
    entry = db.query(AuditEntry).one()
    assert (entry.actor, entry.action, entry.target) == ("manage", "token.create", "bootstrap")
    assert entry.details["kind"] == "global"


def test_api_refusals(signed_in: Callable[..., TestClient], bearer: Bearer, db: Session) -> None:
    lead = signed_in(*LEAD)
    create(lead, kind="team", name="t")
    token = db.query(Token).one()
    member = bearer("pydebuprep-team-web")
    assert lead.get("/api/v1/teams/web/tokens", headers=member).status_code == 403
    assert lead.delete(f"/api/v1/teams/web/tokens/{token.id}", headers=member).status_code == 403
    assert lead.delete("/api/v1/teams/web/tokens/99999", headers=bearer(*LEAD)).status_code == 404
    assert lead.get("/api/v1/teams/nope/tokens", headers=bearer(*LEAD)).status_code == 404


def test_page_refusals(signed_in: Callable[..., TestClient], db: Session) -> None:
    lead = signed_in(*LEAD)
    create(lead, kind="team", name="t")
    token = db.query(Token).one()
    member = signed_in("pydebuprep-team-web")
    resp = member.post(f"/teams/web/tokens/{token.id}/revoke", data={"csrf_token": csrf_of(member)})
    assert resp.status_code == 403
    assert lead.get("/teams/nope").status_code == 404
    for path in ("/teams/nope/tokens", f"/teams/nope/tokens/{token.id}/revoke"):
        assert lead.post(path, data={"csrf_token": csrf_of(lead)}).status_code == 404, path


@pytest.mark.parametrize(
    "fields",
    [{"kind": "global", "team_id": 1}, {"kind": "team"}, {"kind": "global", "host_name": "h"}],
)
def test_create_token_checks_kind_team_and_host(db: Session, fields: dict[str, object]) -> None:
    from pydebuprep_portal.tokens import create_token

    make_team(db, "web")
    with pytest.raises(ValueError):
        create_token(db, name="t", actor="test", expires_at=None, **fields)  # type: ignore[arg-type]
