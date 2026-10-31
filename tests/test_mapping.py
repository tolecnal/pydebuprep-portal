"""The unassigned queue, and mapping hosts to teams."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import assign, csrf_of, make_team
from pydebuprep_portal.models import AuditEntry, Host, Report
from pydebuprep_portal.settings_store import get_settings, update_settings

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]
LEAD = ("pydebuprep-team-web-lead",)
ADMIN = ("pydebuprep-admins",)


@pytest.fixture
def queued(db: Session, add_report: AddReport) -> Host:
    make_team(db, "web")
    make_team(db, "db")
    add_report(hostname="q1", generated_at="2026-10-01T09:00:00+00:00")
    return add_report(hostname="q1").host


def teams_of_reports(db: Session, host: str) -> set[str | None]:
    db.expire_all()
    rows = db.query(Report).join(Report.host).filter(Host.name == host)
    return {r.team.name if r.team else None for r in rows}


def map_form(client: TestClient, host: str, team: str) -> object:
    return client.post(f"/queue/{host}/map", data={"csrf_token": csrf_of(client), "team": team},
                       follow_redirects=False)  # fmt: skip


def only_admins_map(db: Session, value: bool) -> None:
    s = get_settings(db)
    update_settings(db, "test", retention_days=s.retention_days, only_admins_map=value,
                    max_upload_bytes=s.max_upload_bytes)  # fmt: skip


def test_admin_maps_a_queued_host_and_its_reports_move(
    signed_in: Callable[..., TestClient], db: Session, queued: Host
) -> None:
    resp = map_form(signed_in(*ADMIN), "q1", "db")
    assert (resp.status_code, resp.headers["location"]) == (303, "/queue")  # type: ignore[attr-defined]
    assert teams_of_reports(db, "q1") == {"db"}
    entry = db.query(AuditEntry).filter_by(action="host.map").one()
    assert entry.target == "q1"
    assert entry.details == {"team": "db", "from": None, "reports_moved": 2}


def test_lead_maps_into_own_team_only(
    signed_in: Callable[..., TestClient], db: Session, queued: Host
) -> None:
    lead = signed_in(*LEAD)
    assert map_form(lead, "q1", "db").status_code == 403  # type: ignore[attr-defined]
    assert teams_of_reports(db, "q1") == {None}
    assert db.query(AuditEntry).filter_by(action="host.map").count() == 0
    assert map_form(lead, "q1", "web").status_code == 303  # type: ignore[attr-defined]
    assert teams_of_reports(db, "q1") == {"web"}
    assert lead.get("/hosts/q1").status_code == 200  # now the team's


def test_lead_cannot_remap_a_mapped_host(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    assign(db, add_report(hostname="d1"), make_team(db, "db"))
    lead = signed_in(*LEAD)
    assert map_form(lead, "d1", "web").status_code in (403, 404)  # type: ignore[attr-defined]
    assign(db, add_report(hostname="w1"), make_team(db, "web"))
    assert map_form(lead, "w1", "web").status_code == 403  # type: ignore[attr-defined]
    assert teams_of_reports(db, "d1") == {"db"}


def test_only_admins_map_setting_stops_leads(
    signed_in: Callable[..., TestClient], db: Session, queued: Host
) -> None:
    only_admins_map(db, True)
    lead = signed_in(*LEAD)
    assert 'action="/queue/q1/map"' not in lead.get("/queue").text
    assert map_form(lead, "q1", "web").status_code == 403  # type: ignore[attr-defined]
    assert map_form(signed_in(*ADMIN), "q1", "web").status_code == 303  # type: ignore[attr-defined]


def test_admin_remaps_and_moves_all_reports(
    signed_in: Callable[..., TestClient], db: Session, queued: Host
) -> None:
    admin = signed_in(*ADMIN)
    map_form(admin, "q1", "web")
    resp = admin.post("/hosts/q1/map", data={"csrf_token": csrf_of(admin), "team": "db"},
                      follow_redirects=False)  # fmt: skip
    assert (resp.status_code, resp.headers["location"]) == (303, "/hosts/q1")
    assert teams_of_reports(db, "q1") == {"db"}
    last = db.query(AuditEntry).filter_by(action="host.map").order_by(AuditEntry.id.desc()).first()
    assert last is not None and last.details == {"team": "db", "from": "web", "reports_moved": 2}


def test_mapping_to_the_same_team_changes_nothing(
    signed_in: Callable[..., TestClient], db: Session, queued: Host
) -> None:
    admin = signed_in(*ADMIN)
    map_form(admin, "q1", "web")
    map_form(admin, "q1", "web")
    assert db.query(AuditEntry).filter_by(action="host.map").count() == 1


def test_admin_unmaps(
    signed_in: Callable[..., TestClient], db: Session, queued: Host, bearer: Bearer
) -> None:
    admin = signed_in(*ADMIN)
    map_form(admin, "q1", "web")
    resp = admin.post(
        "/hosts/q1/unmap", data={"csrf_token": csrf_of(admin)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert teams_of_reports(db, "q1") == {None}
    entry = db.query(AuditEntry).filter_by(action="host.unmap").one()
    assert entry.details == {"from": "web", "reports_moved": 2}
    lead = signed_in(*LEAD)
    assert lead.post("/hosts/q1/unmap", data={"csrf_token": csrf_of(lead)}).status_code in (
        403,
        404,
    )
    assert admin.delete("/api/v1/hosts/q1/team", headers=bearer(*ADMIN)).status_code == 204


def test_lead_sees_queue_names_not_reports(
    signed_in: Callable[..., TestClient], db: Session, queued: Host, bearer: Bearer
) -> None:
    lead = signed_in(*LEAD)
    page = lead.get("/queue").text
    assert "q1" in page and "debian 13" in page
    assert "/reports/" not in page and 'href="/hosts/q1"' not in page
    assert 'action="/queue/q1/map"' in page
    report = db.query(Report).first()
    assert lead.get(f"/reports/{report.id}").status_code == 404  # type: ignore[union-attr]
    hosts = lead.get("/api/v1/queue", headers=bearer(*LEAD)).json()["hosts"]
    assert [(h["name"], h["reports"]) for h in hosts] == [("q1", None)]
    admin_page = signed_in(*ADMIN).get("/queue").text
    assert 'href="/hosts/q1"' in admin_page


def test_member_and_nobody_have_no_queue(
    signed_in: Callable[..., TestClient], queued: Host, bearer: Bearer
) -> None:
    for groups in (("pydebuprep-team-web",), ()):
        client = signed_in(*groups)
        assert client.get("/queue").status_code == 403
        assert 'href="/queue"' not in client.get("/").text
        assert client.get("/api/v1/queue", headers=bearer(*groups)).status_code == 403


def test_mapping_clears_matching_flags(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    report = assign(db, add_report(hostname="f1"), make_team(db, "web"))
    report.token_team_id = make_team(db, "db").id
    db.commit()
    admin = signed_in(*ADMIN)
    resp = admin.post("/hosts/f1/map", data={"csrf_token": csrf_of(admin), "team": "db"})
    assert resp.status_code in (200, 303)
    db.expire_all()
    assert db.get(Report, report.id).token_team_id is None  # type: ignore[union-attr]


def test_unknown_host_or_team_is_404(
    signed_in: Callable[..., TestClient], queued: Host, bearer: Bearer
) -> None:
    admin = signed_in(*ADMIN)
    assert map_form(admin, "nope", "web").status_code == 404  # type: ignore[attr-defined]
    assert map_form(admin, "q1", "nope").status_code == 404  # type: ignore[attr-defined]
    headers = bearer(*ADMIN)
    assert (
        admin.put("/api/v1/hosts/nope/team", json={"team": "web"}, headers=headers).status_code
        == 404
    )
    assert admin.delete("/api/v1/hosts/nope/team", headers=headers).status_code == 404


def test_api_map(
    signed_in: Callable[..., TestClient], db: Session, queued: Host, bearer: Bearer
) -> None:
    lead = signed_in(*LEAD)
    resp = lead.put("/api/v1/hosts/q1/team", json={"team": "web"}, headers=bearer(*LEAD))
    assert resp.status_code == 200
    assert resp.json() == {"host": "q1", "team": "web", "reports_moved": 2}
    assert (
        lead.put("/api/v1/hosts/q1/team", json={"team": "db"}, headers=bearer(*LEAD)).status_code
        == 403
    )
    assert lead.delete("/api/v1/hosts/q1/team", headers=bearer(*LEAD)).status_code == 403


def test_map_forms_need_csrf(
    signed_in: Callable[..., TestClient], db: Session, queued: Host
) -> None:
    admin = signed_in(*ADMIN)
    assert admin.post("/queue/q1/map", data={"team": "web"}).status_code == 403
    assert admin.post("/hosts/q1/unmap").status_code == 403
    assert teams_of_reports(db, "q1") == {None}


def test_queue_is_empty_for_members(db: Session, queued: Host) -> None:
    from pydebuprep_portal.access import Viewer
    from pydebuprep_portal.mapping import queue

    assert queue(db, Viewer("m", "m", frozenset(), teams=frozenset({"web"}))) == []
    assert [h.name for h in queue(db, Viewer("l", "l", frozenset(), leads=frozenset({"web"})))] == [
        "q1"
    ]


def test_leads_see_no_report_content_in_the_queue(
    signed_in: Callable[..., TestClient], queued: Host, bearer: Bearer
) -> None:
    lead = signed_in(*LEAD)
    page = lead.get("/queue").text
    assert "q1" in page and 'class="sev' not in page and "<th>Reports</th>" not in page
    (host,) = lead.get("/api/v1/queue", headers=bearer(*LEAD)).json()["hosts"]
    assert (host["worst"], host["reports"]) == (None, None)
    (host,) = lead.get("/api/v1/queue", headers=bearer(*ADMIN)).json()["hosts"]
    assert (host["worst"], host["reports"]) == ("critical", 2)


def test_leads_have_no_queue_while_only_admins_map(
    signed_in: Callable[..., TestClient], db: Session, queued: Host, bearer: Bearer
) -> None:
    only_admins_map(db, True)
    lead = signed_in(*LEAD)
    assert lead.get("/queue").status_code == 403
    assert 'href="/queue"' not in lead.get("/").text
    assert lead.get("/api/v1/queue", headers=bearer(*LEAD)).status_code == 403
    assert signed_in(*ADMIN).get("/queue").status_code == 200
