"""Write the smoke test's reports to DIR: two reports of one host name from different
machines, with nginx and Apache findings that carry details and evidence. Built with
pydebuprep's own model, so they are what the pinned pydebuprep would send."""

from __future__ import annotations

import sys
from pathlib import Path

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

HOST = "smoke-web"
MACHINES = ("0f1e2d3c4b5a49788796a5b4c3d2e1f0", "a1b2c3d4e5f647188899aabbccddeeff")
NGINX = Finding(
    Severity.WARNING,
    "nginx passes requests to sockets that don't exist",
    detail="Requests sent there fail (502 Bad Gateway).",
    evidence=["/run/php/php7.4-fpm.sock (fastcgi_pass in /etc/nginx/sites-enabled/shop)"],
)
APACHE = Finding(
    Severity.CRITICAL,
    "Apache loads modules that don't exist",
    evidence=["/usr/lib/apache2/modules/libphp7.4.so doesn't exist (LoadModule php7_module)"],
)


def report(machine: str, day: int) -> str:
    results = [
        CheckResult("nginx", "nginx configuration references", "applications",
                    Status.FINDINGS, findings=[NGINX]),
        CheckResult("apache", "Apache configuration references", "applications",
                    Status.FINDINGS, findings=[APACHE]),
    ]  # fmt: skip
    return render(
        Report(
            tool={"name": "pydebuprep", "version": "smoke"},
            generated_at=f"2026-10-{day:02d}T09:00:00+00:00",
            host=Host(HOST, "debian", "13", "trixie", "6.12.0-1-amd64", True, machine),
            baseline=None,
            summary=compute_summary(results),
            results=results,
        ),
        "json",
    )


def main(out: Path) -> None:
    for day, machine in enumerate(MACHINES, start=1):
        (out / f"{HOST}-{day}.json").write_text(report(machine, day))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
