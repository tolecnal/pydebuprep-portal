"""pydebuprep's own upload code against a running portal: the contract between the two."""

from __future__ import annotations

import threading
import urllib.request

from pydebuprep.portal import Connection, upload_report
from sqlalchemy.orm import Session

from conftest import LivePortal
from pydebuprep_portal.models import Report
from sample import sample_json


def test_pydebuprep_cli_uploads(live_portal: LivePortal, token: str, db: Session) -> None:
    conn = Connection(allow_http=True)
    body = sample_json().encode()
    url = upload_report(live_portal.base_url, token, conn, body)
    assert url.startswith(f"{live_portal.base_url}/reports/")
    assert upload_report(live_portal.base_url, token, conn, body) == url
    assert db.query(Report).count() == 1


def test_concurrent_duplicates_store_one_row(
    live_portal: LivePortal, token: str, db: Session
) -> None:
    body = sample_json(hostname="new-host").encode()
    statuses: list[int] = []

    def send() -> None:
        req = urllib.request.Request(
            f"{live_portal.base_url}/api/v1/reports",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            statuses.append(resp.status)

    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [200] * 7 + [201]
    assert db.query(Report).count() == 1
