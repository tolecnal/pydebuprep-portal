"""Check, inside the portal container, what the smoke test's uploads stored:
  python - FRESH_HOST FRESH_MACHINE_ID < scripts/smoke_check.py
FRESH_MACHINE_ID is what the uploading machine's report should carry ("" for none)."""

from __future__ import annotations

import os
import sys

from sqlalchemy import select

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.db import make_engine, make_sessionmaker
from pydebuprep_portal.models import Host, Report
from pydebuprep_portal.queries import list_hosts, search_findings
from pydebuprep_portal.settings import load_settings

AUDITOR = Viewer("smoke", "smoke", frozenset({"auditor"}))


def fail(message: str) -> None:
    print(f"smoke: {message}", file=sys.stderr)
    sys.exit(1)


def main(fresh_host: str, fresh_machine: str) -> None:
    engine = make_engine(load_settings(os.environ).database_url)
    with make_sessionmaker(engine)() as session:
        stored = session.scalars(
            select(Report.machine_id).join(Report.host).where(Host.name == fresh_host.lower())
        ).all()
        if stored != [fresh_machine or None]:
            fail(f"the fresh report's machine ID is {stored}, expected {fresh_machine!r}")
        machines = {h.name: h.machines for h in list_hosts(session, AUDITOR)}
        if machines.get("smoke-web") != 2:
            fail(f"smoke-web should show 2 machines: {machines}")
        for words, check in (("php7.4-fpm.sock", "nginx"), ("libphp7.4.so", "apache")):
            hits = {h.check_id for h in search_findings(session, AUDITOR, words=words)}
            if hits != {check}:
                fail(f"searching {words!r} found {hits}, expected {check}")
    print("smoke: stored machine IDs, the clash and the findings' evidence")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
