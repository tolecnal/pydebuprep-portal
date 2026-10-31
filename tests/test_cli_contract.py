"""pydebuprep's own upload code against a running portal: the contract between the two."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
from pathlib import Path

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


def test_a_stalled_upload_does_not_block_its_token(live_portal: LivePortal, token: str) -> None:
    host, port = live_portal.base_url.removeprefix("http://").split(":")
    stalled = socket.create_connection((host, int(port)))
    try:
        stalled.sendall(
            b"POST /api/v1/reports HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            + f"Authorization: Bearer {token}\r\nContent-Length: 100000\r\n\r\n".encode()
            + b'{"schema_version"'
        )
        time.sleep(0.3)  # the stalled upload has been authenticated and is reading its body
        url = upload_report(live_portal.base_url, token, Connection(allow_http=True, timeout=5),
                            sample_json().encode())  # fmt: skip
        assert "/reports/" in url
    finally:
        stalled.close()


def test_pydebuprep_fetches_and_accepts_the_config(
    live_portal: LivePortal, fetch_tokens: dict[str, str]
) -> None:
    from pydebuprep.portal import fetch_config
    from pydebuprep.remote_config import parse_remote

    url = f"{live_portal.base_url}/api/v1/configs/web/default"
    fetched = fetch_config(url, fetch_tokens["web-team"], Connection(allow_http=True))
    assert fetched.text is not None
    config = parse_remote(fetched.text, url)
    assert config.report.todo is True
    assert config.upload.url == live_portal.base_url
    again = fetch_config(url, fetch_tokens["web-team"], Connection(allow_http=True), fetched.etag)
    assert again.text is None and again.etag == fetched.etag


def test_pydebuprep_load_remote_config_saves_a_copy(
    live_portal: LivePortal, fetch_tokens: dict[str, str], tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    from pydebuprep.remote_config import load_remote_config

    token_file = tmp_path / "token"
    token_file.write_text(fetch_tokens["global"] + "\n")
    token_file.chmod(0o600)
    cache = tmp_path / "configs"
    cache.mkdir(mode=0o700)
    warnings: list[str] = []
    url = f"{live_portal.base_url}/api/v1/configs/web/default"
    config, info = load_remote_config(url, token_file=str(token_file), conn=Connection(allow_http=True),
                                      cache_dir=str(cache), now=datetime.now(UTC), warn=warnings.append, env={})  # fmt: skip
    assert config.report.todo is True
    assert (info.source, info.stale) == (url, False)
    assert len(list(cache.iterdir())) == 1
    assert warnings == []
