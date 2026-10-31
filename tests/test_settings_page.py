"""The settings page and the retention overrides on team and host pages."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import assign, csrf_of, make_team
from pydebuprep_portal.models import AuditEntry, Host, Report, Team
from pydebuprep_portal.settings_store import GlobalSettings, get_settings

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]
ADMIN = ("pydebuprep-admins",)


def test_settings_page_admin_edits_auditor_reads(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    admin = signed_in(*ADMIN)
    form = {"csrf_token": csrf_of(admin), "retention_days": "", "keep_forever": "1",
            "only_admins_map": "1", "max_upload_mib": "20"}  # fmt: skip
    resp = admin.post("/settings", data=form, follow_redirects=False)
    assert (resp.status_code, resp.headers["location"]) == (303, "/settings")
    assert get_settings(db) == GlobalSettings(None, True, 20 * 1024 * 1024)
    assert db.query(AuditEntry).filter_by(action="settings.update").count() == 1
    auditor = signed_in("pydebuprep-auditors")
    page = auditor.get("/settings").text
    assert "forever" in page and 'action="/settings"' not in page
    assert (
        auditor.post("/settings", data=form | {"csrf_token": csrf_of(auditor)}).status_code == 403
    )
    member = signed_in("pydebuprep-team-web")
    assert member.get("/settings").status_code == 403
    headers = bearer(*ADMIN)
    assert admin.get("/api/v1/settings", headers=headers).json() == {
        "retention_days": None, "only_admins_map": True, "max_upload_bytes": 20971520}  # fmt: skip
    resp = admin.put("/api/v1/settings", headers=headers,
                     json={"retention_days": 30, "only_admins_map": False, "max_upload_bytes": 4096})  # fmt: skip
    assert resp.status_code == 200 and get_settings(db).retention_days == 30
    assert admin.get("/api/v1/settings", headers=bearer("pydebuprep-team-web")).status_code == 403
    assert (
        admin.put(
            "/api/v1/settings", headers=bearer("pydebuprep-auditors"), json=resp.json()
        ).status_code
        == 403
    )


def test_bad_settings_form(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    admin = signed_in(*ADMIN)
    for retention, mib in (("0", "10"), ("ten", "10"), ("30", "0"), ("30", "big")):
        form = {"csrf_token": csrf_of(admin), "retention_days": retention, "max_upload_mib": mib}
        resp = admin.post("/settings", data=form)
        assert resp.status_code == 422 and 'class="notice"' in resp.text
    bad = {"retention_days": 0, "only_admins_map": False, "max_upload_bytes": 4096}
    assert admin.put("/api/v1/settings", json=bad, headers=bearer(*ADMIN)).status_code == 422
    assert get_settings(db).retention_days == 365


def test_retention_override_forms_admin_only(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session, add_report: AddReport
) -> None:
    assign(db, add_report(hostname="w1"), make_team(db, "web"))
    lead = signed_in("pydebuprep-team-web-lead")
    assert 'action="/teams/web/retention"' not in lead.get("/teams/web").text
    resp = lead.post(
        "/teams/web/retention", data={"csrf_token": csrf_of(lead), "mode": "days", "days": "90"}
    )
    assert resp.status_code == 403
    admin = signed_in(*ADMIN)
    resp = admin.post("/teams/web/retention", data={"csrf_token": csrf_of(admin), "mode": "days", "days": "90"},
                      follow_redirects=False)  # fmt: skip
    assert (resp.status_code, resp.headers["location"]) == (303, "/teams/web")
    resp = admin.post("/hosts/w1/retention", data={"csrf_token": csrf_of(admin), "mode": "forever"},
                      follow_redirects=False)  # fmt: skip
    assert (resp.status_code, resp.headers["location"]) == (303, "/hosts/w1")
    db.expire_all()
    assert db.query(Team).filter_by(name="web").one().retention_days == 90
    assert db.query(Host).filter_by(name="w1").one().keep_forever is True
    entries = db.query(AuditEntry).filter_by(action="retention.set").order_by(AuditEntry.id).all()
    assert [e.target for e in entries] == ["team:web", "host:w1"]
    assert entries[0].details == {
        "old": {"days": None, "forever": False},
        "new": {"days": 90, "forever": False},
    }
    headers = bearer(*ADMIN)
    assert (
        admin.put(
            "/api/v1/teams/web/retention", json={"days": None, "forever": False}, headers=headers
        ).status_code
        == 200
    )
    assert (
        admin.put(
            "/api/v1/hosts/w1/retention", json={"days": 7, "forever": False}, headers=headers
        ).status_code
        == 200
    )
    assert (
        admin.put(
            "/api/v1/hosts/w1/retention", json={"days": 0, "forever": False}, headers=headers
        ).status_code
        == 422
    )
    assert (
        admin.put(
            "/api/v1/hosts/nope/retention", json={"days": 7, "forever": False}, headers=headers
        ).status_code
        == 404
    )
    assert admin.put("/api/v1/teams/web/retention", json={"days": 7, "forever": False},
                     headers=bearer("pydebuprep-team-web-lead")).status_code == 403  # fmt: skip
    resp = admin.post(
        "/hosts/w1/retention", data={"csrf_token": csrf_of(admin), "mode": "days", "days": "x"}
    )
    assert resp.status_code == 422


def test_effective_retention_shown(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    team = make_team(db, "web")
    team.retention_days = 90
    assign(db, add_report(hostname="w1"), team)
    host = db.query(Host).filter_by(name="w1").one()
    host.keep_forever = True
    db.commit()
    admin = signed_in(*ADMIN)
    assert "90 days (team)" in admin.get("/teams/web").text
    assert "forever (host)" in admin.get("/hosts/w1").text
    assert "90 days (team)" in admin.get("/teams").text


def test_retention_form_edges(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    assign(db, add_report(hostname="w1"), make_team(db, "web"))
    admin = signed_in(*ADMIN)
    csrf = csrf_of(admin)
    assert (
        admin.post(
            "/teams/web/retention", data={"csrf_token": csrf, "mode": "days", "days": "0"}
        ).status_code
        == 422
    )
    admin.post("/teams/web/retention", data={"csrf_token": csrf, "mode": "days", "days": "30"})
    admin.post("/teams/web/retention", data={"csrf_token": csrf, "mode": "inherit"})
    admin.post("/teams/web/retention", data={"csrf_token": csrf, "mode": "inherit"})  # unchanged
    db.expire_all()
    team = db.query(Team).filter_by(name="web").one()
    assert (team.retention_days, team.keep_forever) == (None, False)
    assert db.query(AuditEntry).filter_by(action="retention.set").count() == 2
    assert (
        admin.post(
            "/hosts/nope/retention", data={"csrf_token": csrf, "mode": "forever"}
        ).status_code
        == 404
    )
    lead = signed_in("pydebuprep-team-web-lead")
    resp = lead.post("/hosts/w1/retention", data={"csrf_token": csrf_of(lead), "mode": "forever"})
    assert resp.status_code == 403


def test_retention_of_an_unknown_team_is_404(signed_in: Callable[..., TestClient]) -> None:
    admin = signed_in(*ADMIN)
    resp = admin.post(
        "/teams/nope/retention", data={"csrf_token": csrf_of(admin), "mode": "forever"}
    )
    assert resp.status_code == 404
