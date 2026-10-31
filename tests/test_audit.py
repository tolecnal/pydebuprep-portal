"""The audit log: who changed what, and when."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import csrf_of
from pydebuprep_portal.access import Viewer
from pydebuprep_portal.audit import actor_of, list_audit, record
from pydebuprep_portal.manage import main
from pydebuprep_portal.models import AuditEntry, Report, Token
from pydebuprep_portal.retention import expire_reports

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]


def test_record_joins_the_callers_transaction(db: Session) -> None:
    record(db, "Ada <user-1>", "team.create", "web", public=False)
    db.rollback()
    assert list_audit(db) == []
    record(db, "Ada <user-1>", "team.create", "web", public=False)
    db.commit()
    (entry,) = list_audit(db)
    assert (entry.actor, entry.action, entry.target, entry.details) == (
        "Ada <user-1>", "team.create", "web", {"public": False},
    )  # fmt: skip
    assert entry.at is not None


def test_list_audit_newest_first_and_paged(db: Session) -> None:
    for i in range(5):
        record(db, "a", "x", f"t{i}")
    db.commit()
    first = list_audit(db, limit=3)
    assert [e.target for e in first] == ["t4", "t3", "t2"]
    assert [e.target for e in list_audit(db, before=first[-1].id)] == ["t1", "t0"]


def test_actor_of() -> None:
    assert actor_of(Viewer("user-1", "Ada", frozenset())) == "Ada <user-1>"


# --- the audit log page and API --------------------------------------------------

ACTIONS = {"settings.update", "team.create", "team.visibility", "token.create", "token.revoke",
           "host.map", "host.unmap", "retention.set", "retention.expire", "report.delete",
           "config.create", "config.save"}  # fmt: skip


def test_audit_page_and_api_for_admins_and_auditors(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    record(db, "Ada <user-1>", "team.create", "ops")
    db.commit()
    for groups, status in ((("pydebuprep-admins",), 200), (("pydebuprep-auditors",), 200),
                           (("pydebuprep-team-web-lead",), 403), ((), 403)):  # fmt: skip
        client = signed_in(*groups)
        resp = client.get("/audit")
        assert resp.status_code == status, groups
        assert client.get("/api/v1/audit", headers=bearer(*groups)).status_code == status
        assert ('href="/audit"' in client.get("/").text) is (status == 200)
    page = signed_in("pydebuprep-auditors").get("/audit").text
    assert "team.create" in page and "ops" in page and "Ada &lt;user-1&gt;" in page


def test_every_change_is_audited(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport, token: str
) -> None:
    admin = signed_in("pydebuprep-admins")

    def post(path: str, **form: str) -> None:
        resp = admin.post(path, data={"csrf_token": csrf_of(admin)} | form, follow_redirects=False)
        assert resp.status_code in (200, 303), (path, resp.status_code)

    post("/settings", retention_days="30", max_upload_mib="10")
    post("/teams", name="ops")
    post("/teams/ops/visibility", public="1")
    post("/teams/ops/tokens", kind="team", name="ops token")
    created = db.query(Token).filter_by(name="ops token").one()
    post(f"/teams/ops/tokens/{created.id}/revoke")
    add_report(hostname="h1", generated_at="2026-01-01T00:00:00+00:00").received_at = datetime(
        2020, 1, 1, tzinfo=UTC
    )
    db.commit()
    newest = add_report(hostname="h1")
    post("/queue/h1/map", team="ops")
    post("/hosts/h1/unmap")
    post("/teams/ops/retention", mode="days", days="90")
    expire_reports(db, datetime.now(UTC))
    post(f"/reports/{newest.id}/delete")
    post("/configs", team="ops", name="default")
    post("/configs/ops/default", _fields="report.todo", **{"report.todo": "1"})
    seen = {e.action for e in list_audit(db, limit=100)}
    assert seen == ACTIONS


def test_audit_filter_and_paging(
    signed_in: Callable[..., TestClient], bearer: Bearer, db: Session
) -> None:
    for i in range(60):
        record(db, "a", "token.create" if i % 2 else "host.map", f"t{i}")
    db.commit()
    admin = signed_in("pydebuprep-admins")
    headers = bearer("pydebuprep-admins")
    data = admin.get("/api/v1/audit", params={"action": "token."}, headers=headers).json()
    assert len(data["entries"]) == 30 and data["next_before"] is None
    assert {e["action"] for e in data["entries"]} == {"token.create"}
    first = admin.get("/api/v1/audit", headers=headers).json()
    assert len(first["entries"]) == 50 and first["next_before"] is not None
    rest = admin.get(
        "/api/v1/audit", params={"before": first["next_before"]}, headers=headers
    ).json()
    assert len(rest["entries"]) == 10
    page = admin.get("/audit", params={"action": "token."}).text
    assert "host.map" not in page
    assert "Older entries" in admin.get("/audit").text


def test_audit_details_are_escaped(signed_in: Callable[..., TestClient], db: Session) -> None:
    record(db, "<script>", "host.map", "<b>host</b>", note="<i>x</i>")
    db.commit()
    resp = signed_in("pydebuprep-auditors").get("/audit")
    assert resp.status_code == 200
    assert "&lt;b&gt;host&lt;/b&gt;" in resp.text
    for raw in ("<script>", "<b>host</b>", "<i>x</i>"):
        assert raw not in resp.text


def test_manage_create_team_token(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-token", "h1 token", "--team", "web", "--host", "H1"]) == 0
    plain = capsys.readouterr().out.strip()
    token = db.query(Token).one()
    assert (token.kind, token.team.name, token.host_name) == ("host", "web", "h1")  # type: ignore[union-attr]
    assert plain.startswith(f"pdp_{token.public_id}_")
    assert main(["create-token", "web token", "--team", "web"]) == 0
    capsys.readouterr()
    assert db.query(Token).filter_by(name="web token").one().kind == "team"
    actions = [(e.actor, e.action) for e in db.query(AuditEntry).order_by(AuditEntry.id)]
    assert actions == [
        ("manage", "team.create"),
        ("manage", "token.create"),
        ("manage", "token.create"),
    ]


@pytest.mark.parametrize(
    "argv", [["create-token", "x", "--host", "h1"], ["create-token", "x", "--team", "Bad Name"]]
)
def test_manage_team_token_errors(db_env: dict[str, str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
