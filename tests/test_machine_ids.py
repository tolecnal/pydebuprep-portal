"""Machine IDs: telling apart machines that report the same host name."""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import Report
from pydebuprep_portal.queries import list_hosts

AddReport = Callable[..., Report]
ADMIN = Viewer("a", "Admin", frozenset({"admin"}))
ONE = "97550a10b77649e0b7a4f6565cb35289"
TWO = "bdeb4ec5d07b4f568e79fb5e3810bbcd"


def day(n: int) -> str:
    return f"2026-10-{n:02d}T09:00:00+00:00"


def test_reports_keep_their_machine_id(add_report: AddReport) -> None:
    assert add_report(machine_id=ONE).machine_id == ONE
    assert add_report(hostname="old", generated_at=day(2)).machine_id is None


def test_a_host_reported_by_two_machines_is_flagged(db: Session, add_report: AddReport) -> None:
    add_report(hostname="web1", generated_at=day(1), machine_id=ONE)
    add_report(hostname="web1", generated_at=day(2), machine_id=TWO)
    add_report(hostname="web1", generated_at=day(3), machine_id=ONE)
    add_report(hostname="db1", generated_at=day(1), machine_id=ONE)
    add_report(hostname="db1", generated_at=day(2))  # from before 0.8.0: no ID, no clash
    hosts = {h.name: h.machines for h in list_hosts(db, ADMIN)}
    assert hosts == {"db1": 1, "web1": 2}


def test_pages_show_the_clash(admin: TestClient, add_report: AddReport) -> None:
    add_report(hostname="web1", generated_at=day(1), machine_id=ONE)
    add_report(hostname="web1", generated_at=day(2), machine_id=TWO)
    add_report(hostname="db1", generated_at=day(1), machine_id=ONE)
    hosts = admin.get("/hosts").text
    row = re.search(r"<tr>\s*<td><a href=\"/hosts/web1\">.*?</tr>", hosts, re.S)
    assert row is not None and "2 machines" in row.group(0)
    assert "2 machines" not in hosts.split('href="/hosts/db1"')[1].split("</tr>")[0]
    page = admin.get("/hosts/web1").text
    assert "come from 2 different machines" in page
    assert ONE[:8] in page and TWO[:8] in page
    assert "come from" not in admin.get("/hosts/db1").text


def test_report_page_shows_the_machine_id(admin: TestClient, add_report: AddReport) -> None:
    report = add_report(machine_id=ONE)
    assert ONE in admin.get(f"/reports/{report.id}").text
