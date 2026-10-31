"""Teams: created at sign-in from the provider's groups."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import assign, csrf_of, login, make_team
from oidc_stub import OIDCStub
from pydebuprep_portal.models import AuditEntry, Report, Team

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]


def test_sign_in_creates_teams_and_stores_memberships(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    login(oidc_client, stub, groups=["pydebuprep-team-web-lead", "pydebuprep-team-db"])
    teams = {t.name: t for t in db.query(Team)}
    assert set(teams) == {"web", "db"}
    assert teams["web"].public is False
    assert teams["web"].created_by == "sign-in"
    page = oidc_client.get("/").text
    assert '<span class="team">web (lead)</span>' in page
    assert '<span class="team">db</span>' in page


def test_sign_in_again_doesnt_duplicate_teams(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    login(oidc_client, stub, groups=["pydebuprep-team-web"])
    login(oidc_client, stub, groups=["pydebuprep-team-web"])
    assert db.query(Team).count() == 1


def test_api_bearer_carries_teams_but_creates_none(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    token = stub.access_token(groups=["pydebuprep-team-web", "pydebuprep-team-db-lead"])
    data = oidc_client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).json()
    assert (data["teams"], data["leads"]) == (["db", "web"], ["db"])
    assert db.query(Team).count() == 0


# --- team pages, visibility, deleting reports --------------------------------------

LEAD = ("pydebuprep-team-web-lead",)
ADMIN = ("pydebuprep-admins",)


def test_new_team_is_private(signed_in: Callable[..., TestClient], db: Session) -> None:
    admin = signed_in(*ADMIN)
    resp = admin.post(
        "/teams", data={"csrf_token": csrf_of(admin), "name": "ops"}, follow_redirects=False
    )
    assert (resp.status_code, resp.headers["location"]) == (303, "/teams/ops")
    team = db.query(Team).filter_by(name="ops").one()
    assert team.public is False
    assert team.created_by == "Ada Admin <user-1>"
    entry = db.query(AuditEntry).filter_by(action="team.create").one()
    assert (entry.target, entry.details) == ("ops", {})


@pytest.mark.parametrize("name", ["", "Web Lead", "x-lead", "web", "-x", "a" * 64])
def test_bad_or_taken_team_names(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session, name: str
) -> None:
    make_team(db, "web")
    admin = signed_in(*ADMIN)
    assert (
        admin.post("/teams", data={"csrf_token": csrf_of(admin), "name": name}).status_code == 422
    )
    assert (
        admin.post("/api/v1/teams", json={"name": name}, headers=bearer(*ADMIN)).status_code == 422
    )
    assert db.query(Team).count() == 1


def test_only_admins_create_teams(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    lead = signed_in(*LEAD)
    assert lead.post("/teams", data={"csrf_token": csrf_of(lead), "name": "ops"}).status_code == 403
    assert (
        lead.post("/api/v1/teams", json={"name": "ops"}, headers=bearer(*LEAD)).status_code == 403
    )
    assert 'action="/teams"' not in lead.get("/teams").text
    resp = lead.post("/api/v1/teams", json={"name": "ops"}, headers=bearer(*ADMIN))
    assert resp.status_code == 201
    assert resp.json()["public"] is False


def test_lead_makes_own_team_public(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    report = assign(db, add_report(hostname="w1"), make_team(db, "web"))
    outsider = signed_in("pydebuprep-team-db")
    assert outsider.get(f"/reports/{report.id}").status_code == 404
    lead = signed_in(*LEAD)
    resp = lead.post("/teams/web/visibility", data={"csrf_token": csrf_of(lead), "public": "1"},
                     follow_redirects=False)  # fmt: skip
    assert (resp.status_code, resp.headers["location"]) == (303, "/teams/web")
    assert outsider.get(f"/reports/{report.id}").status_code == 200
    entry = db.query(AuditEntry).filter_by(action="team.visibility").one()
    assert entry.details == {"old": "private", "new": "public"}
    lead.post("/teams/web/visibility", data={"csrf_token": csrf_of(lead), "public": "1"})
    assert db.query(AuditEntry).filter_by(action="team.visibility").count() == 1  # unchanged


def test_member_cannot_change_visibility(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    make_team(db, "web")
    member = signed_in("pydebuprep-team-web")
    assert 'action="/teams/web/visibility"' not in member.get("/teams/web").text
    assert (
        member.post(
            "/teams/web/visibility", data={"csrf_token": csrf_of(member), "public": "1"}
        ).status_code
        == 403
    )
    headers = bearer("pydebuprep-team-web")
    assert (
        member.patch("/api/v1/teams/web", json={"public": True}, headers=headers).status_code == 403
    )
    assert (
        member.patch("/api/v1/teams/web", json={"public": True}, headers=bearer(*LEAD)).json()[
            "public"
        ]
        is True
    )


def test_teams_list_per_viewer(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session, add_report: AddReport
) -> None:
    assign(db, add_report(hostname="w1"), make_team(db, "web"))
    make_team(db, "db")
    make_team(db, "docs", public=True)
    member = signed_in("pydebuprep-team-web")
    page = member.get("/teams").text
    assert (
        'href="/teams/web"' in page
        and 'href="/teams/docs"' in page
        and 'href="/teams/db"' not in page
    )
    assert member.get("/teams/db").status_code == 404
    teams = member.get("/api/v1/teams", headers=bearer("pydebuprep-team-web")).json()["teams"]
    web = next(t for t in teams if t["name"] == "web")
    assert (web["hosts"], web["reports"], web["retention"]) == (1, 1, "365 days (global)")
    assert {t["name"] for t in teams} == {"web", "docs"}
    everyone = signed_in("pydebuprep-auditors").get(
        "/api/v1/teams", headers=bearer("pydebuprep-auditors")
    )
    assert {t["name"] for t in everyone.json()["teams"]} == {"web", "db", "docs"}
    assert 'href="/hosts/w1"' in member.get("/teams/web").text


def test_delete_report_permissions(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session, add_report: AddReport
) -> None:
    web = make_team(db, "web")
    reports = [assign(db, add_report(hostname=f"w{i}"), web) for i in range(4)]
    queued = add_report(hostname="q1")
    for groups, report, status in [
        (("pydebuprep-team-web",), reports[0], 403),
        (("pydebuprep-auditors",), reports[0], 403),
        (("pydebuprep-team-db-lead",), reports[0], 404),
        (LEAD, queued, 404),
    ]:
        client = signed_in(*groups)
        assert (
            f'action="/reports/{report.id}/delete"' not in client.get(f"/reports/{report.id}").text
        )
        resp = client.post(f"/reports/{report.id}/delete", data={"csrf_token": csrf_of(client)})
        assert resp.status_code == status, groups
        assert (
            client.delete(f"/api/v1/reports/{report.id}", headers=bearer(*groups)).status_code
            == status
        )
    assert db.query(Report).count() == 5
    lead = signed_in(*LEAD)
    assert f'action="/reports/{reports[0].id}/delete"' in lead.get(f"/reports/{reports[0].id}").text
    resp = lead.post(
        f"/reports/{reports[0].id}/delete",
        data={"csrf_token": csrf_of(lead)},
        follow_redirects=False,
    )
    assert (resp.status_code, resp.headers["location"]) == (303, "/")
    assert lead.delete(f"/api/v1/reports/{reports[1].id}", headers=bearer(*LEAD)).status_code == 204
    admin = signed_in(*ADMIN)
    assert admin.delete(f"/api/v1/reports/{queued.id}", headers=bearer(*ADMIN)).status_code == 204
    assert db.query(Report).count() == 2
    entries = db.query(AuditEntry).filter_by(action="report.delete").all()
    assert {e.target for e in entries} == {"w0", "w1", "q1"}
    assert all("generated_at" in e.details and "id" in e.details for e in entries)


def test_deleting_the_last_report_keeps_the_host_page_404(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    report = add_report(hostname="gone")
    admin = signed_in(*ADMIN)
    admin.post(f"/reports/{report.id}/delete", data={"csrf_token": csrf_of(admin)})
    assert admin.get("/hosts/gone").status_code == 404
    assert (
        admin.post(f"/reports/{report.id}/delete", data={"csrf_token": csrf_of(admin)}).status_code
        == 404
    )


def test_visibility_of_an_unknown_team_is_404(signed_in: Callable[..., TestClient]) -> None:
    admin = signed_in(*ADMIN)
    assert (
        admin.post(
            "/teams/nope/visibility", data={"csrf_token": csrf_of(admin), "public": "1"}
        ).status_code
        == 404
    )


def test_teams_made_at_sign_in_are_audited(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    make_team(db, "old")
    login(oidc_client, stub, groups=["pydebuprep-team-old", "pydebuprep-team-new"])
    entries = [(e.actor, e.action, e.target) for e in db.query(AuditEntry)]
    assert entries == [("sign-in", "team.create", "new")]


def test_too_many_teams_for_a_session_cookie(oidc_client: TestClient, stub: OIDCStub) -> None:
    groups = [f"pydebuprep-team-{'t' * 50}{i:03d}" for i in range(80)]
    resp = login(oidc_client, stub, groups=groups)
    assert resp.status_code == 400
    assert "too many teams" in resp.text


def test_token_audit_names_the_team(db: Session) -> None:
    from pydebuprep_portal.tokens import create_token

    web = make_team(db, "web")
    create_token(db, kind="team", name="t", actor="x", expires_at=None, team_id=web.id)
    entry = db.query(AuditEntry).filter_by(action="token.create").one()
    assert entry.details["team"] == "web" and "team_id" not in entry.details
