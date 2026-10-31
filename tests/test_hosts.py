"""Hosts, their newest report, and their timeline."""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import Report
from pydebuprep_portal.queries import host_timeline, list_hosts

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]
ADMIN = Viewer("a", "Admin", frozenset({"admin"}))
MEMBER = Viewer("m", "Member", frozenset())
OK: tuple = ()
WARN = (("disk-space", "warning", "Disk is full on /var"),)


def day(n: int) -> str:
    return f"2026-10-{n:02d}T09:00:00+00:00"


def test_hosts_with_newest_and_previous_worst(db: Session, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(1), findings=OK)
    newest = add_report(hostname="a", generated_at=day(2), findings=WARN)
    add_report(hostname="b", generated_at=day(1), findings=WARN)
    hosts = {h.name: h for h in list_hosts(db, ADMIN)}
    assert list(hosts) == ["a", "b"]
    assert (hosts["a"].last_report_id, hosts["a"].worst, hosts["a"].previous_worst, hosts["a"].worse) == (
        newest.id, "warning", "ok", True,
    )  # fmt: skip
    assert (hosts["b"].previous_worst, hosts["b"].worse) == (None, False)


def test_newest_is_by_generated_time_not_upload_order(db: Session, add_report: AddReport) -> None:
    newer = add_report(hostname="a", generated_at=day(5), findings=OK)
    add_report(hostname="a", generated_at=day(1), findings=WARN)  # an old one, sent late
    host = list_hosts(db, ADMIN)[0]
    assert (host.last_report_id, host.worst, host.worse) == (newer.id, "ok", False)


def test_worse_only_filter(db: Session, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(1), findings=OK)
    add_report(hostname="a", generated_at=day(2), findings=WARN)
    add_report(hostname="b", generated_at=day(1), findings=WARN)
    add_report(hostname="b", generated_at=day(2), findings=OK)
    add_report(hostname="c", generated_at=day(1), findings=WARN)
    assert [h.name for h in list_hosts(db, ADMIN, worse_only=True)] == ["a"]


def test_host_name_search_escapes_wildcards(db: Session, add_report: AddReport) -> None:
    add_report(hostname="web1")
    add_report(hostname="web_2")
    assert [h.name for h in list_hosts(db, ADMIN, name="WEB")] == ["web1", "web_2"]
    assert [h.name for h in list_hosts(db, ADMIN, name="web_")] == ["web_2"]
    assert [h.name for h in list_hosts(db, ADMIN, name="%")] == []


def test_timeline_oldest_first_with_os_and_kernel(db: Session, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(2), os_version="13", kernel="6.12")
    add_report(hostname="a", generated_at=day(1), os_version="12", kernel="6.1")
    rows = host_timeline(db, ADMIN, "A")
    assert rows is not None
    assert [(r.os, r.kernel) for r in rows] == [
        ("debian 12 (trixie)", "6.1"),
        ("debian 13 (trixie)", "6.12"),
    ]


def test_unknown_or_invisible_host(db: Session, add_report: AddReport) -> None:
    add_report(hostname="a")
    assert host_timeline(db, ADMIN, "nope") is None
    assert host_timeline(db, MEMBER, "a") is None
    assert list_hosts(db, MEMBER) == []


def test_host_pages(admin: TestClient, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(1), findings=OK)
    newest = add_report(hostname="a", generated_at=day(2), findings=WARN)
    page = admin.get("/hosts").text
    assert 'href="/hosts/a"' in page and f'href="/reports/{newest.id}"' in page
    assert "worse" in page
    assert 'href="/hosts/a"' in admin.get("/hosts", params={"worse": "1"}).text
    assert 'href="/hosts/a"' not in admin.get("/hosts", params={"name": "zzz"}).text
    timeline = admin.get("/hosts/a").text
    assert re.findall(r'href="/reports/(\d+)"', timeline)[-1] == str(newest.id)
    assert "6.12.0-1-amd64" in timeline
    assert admin.get("/hosts/nope").status_code == 404


def test_hosts_api(admin: TestClient, add_report: AddReport, bearer: Bearer) -> None:
    add_report(hostname="a", generated_at=day(1), findings=OK)
    newest = add_report(hostname="a", generated_at=day(2), findings=WARN)
    headers = bearer("pydebuprep-auditors")
    hosts = admin.get("/api/v1/hosts", params={"worse_only": "true"}, headers=headers).json()[
        "hosts"
    ]
    assert hosts == [{"name": "a", "last_report_id": newest.id, "last_generated_at": "2026-10-02T09:00:00Z",
                      "worst": "warning", "previous_worst": "ok", "worse": True}]  # fmt: skip
    timeline = admin.get("/api/v1/hosts/a/timeline", headers=headers).json()
    assert timeline["host"] == "a"
    assert [r["id"] for r in timeline["reports"]][-1] == newest.id
    assert timeline["reports"][0]["kernel"] == "6.12.0-1-amd64"
    assert admin.get("/api/v1/hosts/nope/timeline", headers=headers).status_code == 404
    member = bearer()
    assert admin.get("/api/v1/hosts", headers=member).json()["hosts"] == []
    assert admin.get("/api/v1/hosts/a/timeline", headers=member).status_code == 404
