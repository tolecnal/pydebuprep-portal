"""Sample pydebuprep reports, built with pydebuprep's own model and renderer."""

from __future__ import annotations

from pydebuprep.model import (
    CheckResult,
    ConfigInfo,
    Finding,
    Host,
    Report,
    Severity,
    Status,
    compute_summary,
)
from pydebuprep.render import render

DEFAULT_FINDINGS = (
    ("apt-sources", "critical", "Repository still targets bookworm"),
    ("disk-space", "warning", "Disk is full on /var"),
)


def sample_report(
    *,
    hostname: str = "web1",
    generated_at: str = "2026-10-31T09:00:00+00:00",
    findings: tuple[tuple[str, ...], ...] = DEFAULT_FINDINGS,
    os_version: str = "13",
    codename: str = "trixie",
    kernel: str = "6.12.0-1-amd64",
    stale: bool = False,
    machine_id: str | None = None,
) -> Report:
    """FINDINGS are (check, severity, title), optionally followed by a detail and
    evidence lines."""
    by_check: dict[str, list[Finding]] = {}
    for check, severity, title, *more in findings:
        detail = more[0] if more else ""
        by_check.setdefault(check, []).append(
            Finding(Severity(severity), title, detail=detail, evidence=list(more[1:]))
        )
    results = [
        CheckResult(check, check, "system", Status.FINDINGS, findings=items)
        for check, items in by_check.items()
    ]
    results.append(CheckResult("fstab", "fstab", "storage", Status.OK))
    return Report(
        tool={"name": "pydebuprep", "version": "0.5.0"},
        generated_at=generated_at,
        host=Host(hostname, "debian", os_version, codename, kernel, True, machine_id),
        baseline=None,
        summary=compute_summary(results),
        results=results,
        config=ConfigInfo("https://portal.test/c", generated_at, True) if stale else None,
    )


def sample_json(**kw: object) -> str:
    return render(sample_report(**kw), "json")  # type: ignore[arg-type]
