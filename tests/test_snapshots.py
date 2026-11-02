"""Snapshots: what a report was compared with, and which findings are changes since."""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi.testclient import TestClient
from pydebuprep.model import BaselineInfo
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import Report
from pydebuprep_portal.queries import host_timeline, search_findings

AddReport = Callable[..., Report]
ADMIN = Viewer("a", "Admin", frozenset({"admin"}))
MACHINE = "97550a10b77649e0b7a4f6565cb35289"


def snapshot(version: str, taken: str, host: str = "web1") -> BaselineInfo:
    return BaselineInfo(f"/var/lib/pydebuprep/snapshots/{taken}.json", taken, version, host)


def ubuntu_chain(add_report: AddReport) -> list[Report]:
    """One machine upgraded 22.04 → 24.04 → 26.04, a snapshot before each step."""
    common = {"hostname": "web1", "machine_id": MACHINE}
    return [
        add_report(**common, generated_at="2026-10-01T09:00:00+00:00", os_version="22.04",
                   codename="jammy"),
        add_report(**common, generated_at="2026-10-10T09:00:00+00:00", os_version="24.04",
                   codename="noble", baseline=snapshot("22.04", "2026-10-09T20:00:00+00:00"),
                   changes=(("systemd-failed", "critical", "nginx.service was active before"),
                            ("network", "warning", "eth1 lost its address"))),
        add_report(**common, generated_at="2026-10-20T09:00:00+00:00", os_version="26.04",
                   codename="resolute", baseline=snapshot("24.04", "2026-10-19T20:00:00+00:00"),
                   changes=(("php", "warning", "PHP extensions enabled before aren't now"),)),
    ]  # fmt: skip


def test_reports_keep_their_snapshot(add_report: AddReport) -> None:
    none, first, second = ubuntu_chain(add_report)
    assert (none.baseline, none.changes) == (False, 0)
    assert first.baseline and first.baseline_os_version == "22.04"
    assert first.baseline_host == "web1" and first.changes == 2
    assert first.baseline_at is not None and first.baseline_at.isoformat().startswith(
        "2026-10-09T20:00"
    )
    assert second.changes == 1
    assert sorted(f.title for f in first.findings if f.since_baseline) == [
        "eth1 lost its address", "nginx.service was active before",
    ]  # fmt: skip
    assert not [f for f in first.findings if not f.since_baseline and "lost" in f.title]


def test_timeline_reads_as_a_chain_of_upgrades(db: Session, add_report: AddReport) -> None:
    ubuntu_chain(add_report)
    rows = host_timeline(db, ADMIN, "web1")
    assert rows is not None
    assert [(r.baseline_os_version, r.os_version, r.comparison) for r in rows] == [
        (None, "22.04", "none"),
        ("22.04", "24.04", "upgrade"),
        ("24.04", "26.04", "upgrade"),
    ]


def test_suspect_snapshots(db: Session, add_report: AddReport) -> None:
    same = add_report(
        hostname="db1", os_version="13", baseline=snapshot("13", "2026-10-30T09:00:00+00:00", "db1")
    )
    other = add_report(hostname="db2", generated_at="2026-10-31T10:00:00+00:00", os_version="13",
                       baseline=snapshot("12", "2026-10-30T09:00:00+00:00", "db9"))  # fmt: skip
    rows = {r.host: r for name in ("db1", "db2") for r in host_timeline(db, ADMIN, name) or []}
    assert rows["db1"].comparison == "same-release"
    assert rows["db2"].comparison == "other-host"
    assert same.id and other.id


def test_host_page_shows_the_chain(admin: TestClient, add_report: AddReport) -> None:
    ubuntu_chain(add_report)
    page = admin.get("/hosts/web1").text
    assert "<th>Snapshot</th>" in page
    assert re.search(r"22\.04 → 24\.04.*?2 changes", page, re.S)
    assert re.search(r"24\.04 → 26\.04.*?1 change\b", page, re.S)


def test_report_page_says_what_it_was_compared_with(
    admin: TestClient, add_report: AddReport
) -> None:
    none, first, _ = ubuntu_chain(add_report)
    page = admin.get(f"/reports/{first.id}").text
    assert "Compared with" in page
    assert "snapshot taken 2026-10-09 20:00 UTC on 22.04" in page
    assert "upgrade from 22.04 to 24.04" in page
    assert re.search(r"Changes since the snapshot</div><div class=\"value\">2<", page)
    changes = page.split('id="changes"')[1].split("</table>")[0]
    assert "nginx.service was active before" in changes and "eth1 lost its address" in changes
    assert "Disk is full" not in changes  # a problem now, not a change
    plain = admin.get(f"/reports/{none.id}").text
    assert "No snapshot" in plain and 'id="changes"' not in plain


def test_report_page_warns_about_suspect_snapshots(
    admin: TestClient, add_report: AddReport
) -> None:
    same = add_report(
        hostname="db1", os_version="13", baseline=snapshot("13", "2026-10-30T09:00:00+00:00", "db1")
    )
    other = add_report(hostname="db2", os_version="13", generated_at="2026-10-31T10:00:00+00:00",
                       baseline=snapshot("12", "2026-10-30T09:00:00+00:00", "db9"))  # fmt: skip
    assert "taken on the same release" in admin.get(f"/reports/{same.id}").text
    assert "taken on another host (db9)" in admin.get(f"/reports/{other.id}").text


def test_search_for_changes_only(db: Session, admin: TestClient, add_report: AddReport) -> None:
    ubuntu_chain(add_report)
    hits = search_findings(db, ADMIN, changes_only=True)
    assert sorted(h.title for h in hits) == [
        "PHP extensions enabled before aren't now", "eth1 lost its address",
        "nginx.service was active before",
    ]  # fmt: skip
    assert all(h.since_baseline for h in hits)
    page = admin.get("/search?changes=1").text
    assert 'name="changes"' in page and "eth1 lost its address" in page
    assert "Disk is full" not in page
    csv = admin.get("/search?changes=1&format=csv").text
    assert csv.splitlines()[0].endswith(",since_snapshot") and ",yes" in csv


def test_api_has_the_snapshot(
    admin: TestClient, bearer: Callable[..., dict[str, str]], add_report: AddReport
) -> None:
    _, first, _ = ubuntu_chain(add_report)
    headers = bearer("pydebuprep-admins")
    body = admin.get(f"/api/v1/reports/{first.id}", headers=headers).json()
    assert body["baseline"] is True and body["baseline_os_version"] == "22.04"
    assert (
        body["baseline_host"] == "web1" and body["changes"] == 2 and body["comparison"] == "upgrade"
    )
    found = admin.get("/api/v1/search/findings?changes_only=true", headers=headers).json()
    assert {f["title"] for f in found["findings"]} >= {"eth1 lost its address"}
    assert all(f["since_baseline"] for f in found["findings"])


def test_a_snapshot_that_doesnt_say_when_or_where(
    db: Session, admin: TestClient, add_report: AddReport
) -> None:
    vague = BaselineInfo("/s.json", "yesterday", "", "")
    report = add_report(baseline=vague, changes=(("network", "warning", "eth1 lost its address"),))
    assert (report.baseline, report.baseline_at, report.baseline_os_version) == (True, None, None)
    [row] = host_timeline(db, ADMIN, "web1") or []
    assert row.comparison == "compared"
    assert "1 change<" in admin.get("/hosts/web1").text
    assert "The snapshot taken at an unknown time." in admin.get(f"/reports/{report.id}").text
