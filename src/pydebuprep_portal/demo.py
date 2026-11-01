"""Demo data for a local preview: teams, hosts with a few weeks of reports, a config."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from pydebuprep.model import (
    CheckResult,
    Finding,
    Host,
    Report,
    Severity,
    Status,
    compute_summary,
)
from pydebuprep.render import render
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.configs import create_config
from pydebuprep_portal.ingest import store_report
from pydebuprep_portal.models import Report as StoredReport
from pydebuprep_portal.settings import Settings
from pydebuprep_portal.teams import create_team, get_team, set_visibility
from pydebuprep_portal.tokens import create_token

ADMIN = Viewer("demo", "demo", frozenset({"admin"}))
FINDINGS: dict[str, list[tuple[str, ...]]] = {
    "ok": [],
    "info": [("apt-autoremove", "info", "3 packages can be autoremoved")],
    "warning": [
        ("disk-space", "warning", "/var is 87 % full"),
        ("journal-errors", "warning", "12 errors from systemd-resolved in this boot"),
    ],
    "critical": [
        ("apt-sources", "critical", "Repository still targets bookworm"),
        ("systemd-failed", "critical", "nginx.service failed"),
        ("nginx", "warning", "nginx passes requests to sockets that don't exist",
         "Requests sent there fail (502 Bad Gateway).",
         "/run/php/php8.2-fpm.sock (fastcgi_pass in /etc/nginx/sites-enabled/shop); "
         "/run/php has php8.4-fpm.sock"),
        ("disk-space", "warning", "/var is 91 % full"),
    ],
}  # fmt: skip
# Hosts that stay on the old release, and hosts whose name a second machine took over
# for the last reports (to show a clash).
NOT_UPGRADED = {"docs1"}
SECOND_MACHINE = {"web2": 2}
# Each host's worst severity, week by week (oldest first).
STORIES = {
    "web1": ("web", ["ok", "info", "info", "warning", "warning", "critical"]),
    "web2": ("web", ["warning", "warning", "info", "ok", "ok", "ok"]),
    "db1": ("db", ["info", "info", "warning", "info", "info", "info"]),
    "docs1": ("docs", ["ok", "ok", "ok", "ok", "info", "ok"]),
    "new-host": (None, ["warning", "critical"]),
}


class DemoError(Exception):
    pass


def _machine(hostname: str, which: int) -> str:
    return hashlib.sha256(f"{hostname}/{which}".encode()).hexdigest()[:32]


def _report(hostname: str, when: datetime, worst: str, week: int, machine: str) -> str:
    by_check: dict[str, list[Finding]] = {}
    for check, severity, title, *more in FINDINGS[worst]:
        detail, evidence = (more[0], [more[1]]) if more else ("", [])
        by_check.setdefault(check, []).append(
            Finding(Severity(severity), title, detail=detail, evidence=evidence)
        )
    results = [
        CheckResult(check, check, "system", Status.FINDINGS, findings=items)
        for check, items in by_check.items()
    ]
    results.append(CheckResult("fstab", "fstab", "storage", Status.OK))
    old = week < 3 or hostname in NOT_UPGRADED
    kernel = "6.1.0-28-amd64" if old else "6.12.12-amd64"
    release = ("12", "bookworm") if old else ("13", "trixie")
    report = Report(
        tool={"name": "pydebuprep", "version": "0.6.0"},
        generated_at=when.isoformat(),
        host=Host(hostname, "debian", release[0], release[1], kernel, True, machine),
        baseline=None,
        summary=compute_summary(results),
        results=results,
    )
    return render(report, "json")


def load_demo_data(session: Session, settings: Settings, now: datetime | None = None) -> str:
    """Fill an empty portal; a summary of what was made. DemoError if it has reports."""
    if session.scalar(select(StoredReport.id).limit(1)) is not None:
        raise DemoError("the portal already has reports; demo data goes into an empty one")
    now = now or datetime.now(UTC)
    tokens = {}
    for name in ("web", "db", "docs"):
        team = get_team(session, name) or create_team(session, "demo", name)
        if name == "docs":
            set_visibility(session, "demo", team, True)
        tokens[name] = create_token(
            session, kind="team", name=f"{name} hosts", actor="demo", expires_at=None,
            team_id=team.id,
        )[0].id  # fmt: skip
    fleet, _ = create_token(
        session, kind="global", name="demo fleet", actor="demo", expires_at=None
    )
    stored = 0
    for hostname, (team_name, story) in STORIES.items():
        for week, worst in enumerate(story):
            when = now - timedelta(weeks=len(story) - week, hours=len(hostname))
            token_id = tokens[team_name] if team_name else fleet.id
            second = week >= len(story) - SECOND_MACHINE.get(hostname, 0)
            machine = _machine(hostname, 2 if second else 1)
            body = _report(hostname, when, worst, week + 6 - len(story), machine).encode()
            store_report(session, body, token_id, when + timedelta(minutes=5))
            stored += 1
    create_config(
        session, ADMIN, settings, "web", "default",
        {"report": {"min_severity": "warning", "todo": True}}, actor="demo",
    )  # fmt: skip
    return (
        f"Demo data: 3 teams, {len(STORIES)} hosts, {stored} reports, 1 config. "
        "Create an upload token with: manage create-token NAME"
    )
