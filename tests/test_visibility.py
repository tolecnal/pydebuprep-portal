"""Reports are visible by team: own teams, public teams; the queue to admins and auditors."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import assign, make_team
from pydebuprep_portal.models import Report

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]
GROUPS = {
    "web-member": ("pydebuprep-team-web",),
    "web-lead": ("pydebuprep-team-web-lead",),
    "docs-member": ("pydebuprep-team-docs",),
    "nobody": (),
    "admin": ("pydebuprep-admins",),
    "auditor": ("pydebuprep-auditors",),
}


@pytest.fixture
def world(db: Session, add_report: AddReport) -> dict[str, Report]:
    """w1 in web (private), d1 in db (private), x1 in docs (public), q1 queued."""
    web, dbt, docs = make_team(db, "web"), make_team(db, "db"), make_team(db, "docs", public=True)
    return {
        "w1": assign(db, add_report(hostname="w1"), web),
        "d1": assign(db, add_report(hostname="d1"), dbt),
        "x1": assign(db, add_report(hostname="x1"), docs),
        "q1": add_report(hostname="q1"),
    }


def hosts_on(page: str) -> set[str]:
    return set(re.findall(r'href="/hosts/([^"]+)"', page))


@pytest.mark.parametrize(
    ("who", "hosts"),
    [
        ("web-member", {"w1", "x1"}),
        ("web-lead", {"w1", "x1"}),
        ("docs-member", {"x1"}),
        ("nobody", {"x1"}),
        ("admin", {"w1", "d1", "x1", "q1"}),
        ("auditor", {"w1", "d1", "x1", "q1"}),
    ],
)
def test_reports_list_shows_exactly_the_visible_teams(
    signed_in: Callable[..., TestClient],
    bearer: Bearer,
    world: dict[str, Report],
    who: str,
    hosts: set[str],
) -> None:
    client = signed_in(*GROUPS[who])
    assert hosts_on(client.get("/").text) == hosts
    data = client.get("/api/v1/reports", headers=bearer(*GROUPS[who])).json()
    assert {r["host"] for r in data["reports"]} == hosts


@pytest.mark.parametrize(
    "path",
    [
        "/reports/{id}",
        "/reports/{id}/html",
        "/reports/{id}/download/json",
        "/api/v1/reports/{id}",
        "/api/v1/reports/{id}/json",
    ],
)
def test_another_teams_private_report_is_404_everywhere(
    signed_in: Callable[..., TestClient], bearer: Bearer, world: dict[str, Report], path: str
) -> None:
    client = signed_in(*GROUPS["web-member"])
    headers = bearer(*GROUPS["web-member"])
    for report, status in (
        (world["d1"], 404),
        (world["q1"], 404),
        (world["w1"], 200),
        (world["x1"], 200),
    ):
        assert client.get(path.format(id=report.id), headers=headers).status_code == status, (
            report.host.name
        )


def test_queued_reports_only_for_admins_and_auditors(
    signed_in: Callable[..., TestClient], world: dict[str, Report]
) -> None:
    for who, status in (("admin", 200), ("auditor", 200), ("web-lead", 404), ("nobody", 404)):
        assert signed_in(*GROUPS[who]).get(f"/reports/{world['q1'].id}").status_code == status, who


def test_hosts_timeline_and_search_respect_visibility(
    signed_in: Callable[..., TestClient], bearer: Bearer, world: dict[str, Report]
) -> None:
    client = signed_in(*GROUPS["web-member"])
    headers = bearer(*GROUPS["web-member"])
    assert hosts_on(client.get("/hosts").text) == {"w1", "x1"}
    assert {h["name"] for h in client.get("/api/v1/hosts", headers=headers).json()["hosts"]} == {
        "w1",
        "x1",
    }
    assert client.get("/hosts/d1").status_code == 404
    assert client.get("/hosts/w1").status_code == 200
    assert client.get("/api/v1/hosts/d1/timeline", headers=headers).status_code == 404
    found = client.get("/api/v1/search/findings", params={"words": "disk"}, headers=headers).json()
    assert {f["host"] for f in found["findings"]} == {"w1", "x1"}
    assert hosts_on(client.get("/search", params={"words": "disk"}).text) == {"w1", "x1"}


def test_public_team_visible_to_everyone_signed_in(
    signed_in: Callable[..., TestClient], world: dict[str, Report]
) -> None:
    assert signed_in().get(f"/reports/{world['x1'].id}").status_code == 200


def test_filter_by_team_and_unassigned(
    signed_in: Callable[..., TestClient], bearer: Bearer, world: dict[str, Report]
) -> None:
    admin = signed_in(*GROUPS["admin"])
    assert hosts_on(admin.get("/", params={"team": "web"}).text) == {"w1"}
    assert hosts_on(admin.get("/", params={"team": "unassigned"}).text) == {"q1"}
    data = admin.get(
        "/api/v1/reports", params={"team": "unassigned"}, headers=bearer(*GROUPS["admin"])
    ).json()
    assert [r["host"] for r in data["reports"]] == ["q1"]
    assert data["reports"][0]["team"] is None
    member = signed_in(*GROUPS["web-member"])
    assert hosts_on(member.get("/", params={"team": "unassigned"}).text) == set()
    assert hosts_on(member.get("/", params={"team": "db"}).text) == set()
    page = member.get("/").text
    assert '<option value="unassigned"' not in page
    assert '<option value="web"' in page and '<option value="docs"' in page
    assert '<option value="db"' not in page


def test_flagged_filter_and_label(
    signed_in: Callable[..., TestClient], db: Session, world: dict[str, Report]
) -> None:
    flagged = world["w1"]
    flagged.token_team_id = make_team(db, "db").id
    db.commit()
    admin = signed_in(*GROUPS["admin"])
    assert hosts_on(admin.get("/", params={"flagged": "1"}).text) == {"w1"}
    assert "sent with team db's token" in admin.get(f"/reports/{flagged.id}").text
    page = admin.get(f"/reports/{world['q1'].id}").text
    assert "unassigned" in page
