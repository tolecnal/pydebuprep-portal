"""Reading and storing uploaded reports."""

from __future__ import annotations

import hashlib
import zlib
from datetime import UTC, datetime

from pydebuprep.model import Report as ReportData
from pydebuprep.model import Severity, all_findings, compute_summary
from pydebuprep.reportfile import ReportFileError, load_report
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request

from pydebuprep_portal.models import Finding, Host, Report, Token

MAX_HOSTNAME = 253
ENCODINGS = ("identity", "gzip")


class IngestError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def check_headers(request: Request, limit: int) -> None:
    """Refuse a wrong media type or encoding, or a declared size over LIMIT, before reading."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise IngestError(415, "a report is sent as Content-Type: application/json")
    if request.headers.get("content-encoding", "identity").strip().lower() not in ENCODINGS:
        raise IngestError(415, "a report is sent plain or with Content-Encoding: gzip")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise IngestError(413, f"a report may be at most {limit} bytes")


async def read_body(request: Request, limit: int) -> bytes:
    """The body, gunzipped when sent with gzip; at most LIMIT bytes both before and after."""
    too_big = IngestError(413, f"a report may be at most {limit} bytes")
    gzipped = request.headers.get("content-encoding", "identity").strip().lower() == "gzip"
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise too_big
            # Never inflate more than one byte past the limit, however small the input.
            out += inflate.decompress(chunk, limit + 1 - len(out)) if gzipped else chunk
            if len(out) > limit or inflate.unconsumed_tail:
                raise too_big
        # Output held back by max_length stays in unconsumed_tail, checked above:
        # nothing is left to inflate here.
        if gzipped and not inflate.eof:
            raise IngestError(400, "the gzip data is cut short")
    except zlib.error:
        raise IngestError(400, "the body is not valid gzip data") from None
    return bytes(out)


def parse(body: bytes) -> ReportData:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise IngestError(422, "the report is not UTF-8 text") from None
    if not text.lstrip().startswith("{"):
        raise IngestError(422, "expected a JSON report object")
    try:
        report = load_report(text)
    except ReportFileError as exc:
        raise IngestError(422, f"not a pydebuprep report: {exc}") from None
    except RecursionError:
        raise IngestError(422, "the report is nested too deeply") from None
    hostname = report.host.hostname
    if not valid_host_name(hostname):
        raise IngestError(422, f"host.hostname is not a host name: {hostname!r}")
    if _has_nul(report.to_dict()):
        raise IngestError(422, "the report contains a NUL character, which can't be stored")
    return report


def valid_host_name(name: str) -> bool:
    """A host name as reports carry it: printable, no whitespace, at most 253 characters."""
    return (
        bool(name)
        and len(name) <= MAX_HOSTNAME
        and name.isprintable()
        and not any(c.isspace() for c in name)
    )


def _has_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def _when(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _worst(critical: int, warning: int, info: int) -> str:
    if critical:
        return Severity.CRITICAL.value
    if warning:
        return Severity.WARNING.value
    return Severity.INFO.value if info else "ok"


def store_report(
    session: Session, body: bytes, token_id: int, now: datetime
) -> tuple[Report, bool]:
    """Store an upload sent with TOKEN_ID; (report, True) if new, (the stored one, False)
    if sent before. Either way the token's last use is recorded."""
    data = parse(body)
    sha256 = hashlib.sha256(body).hexdigest()
    # Two tries: a concurrent upload may create the same report or the same host first.
    for _ in range(2):
        existing = session.scalar(select(Report).where(Report.sha256 == sha256))
        if existing is not None:
            _used(session, token_id, now)
            session.commit()
            return existing, False
        try:
            report = _insert(
                session, data=data, text=body.decode(), sha256=sha256, token_id=token_id, now=now
            )
        except IntegrityError:
            session.rollback()
        else:
            return report, True
    raise IngestError(503, "the report could not be stored just now; send it again")


def _insert(
    session: Session, *, data: ReportData, text: str, sha256: str, token_id: int, now: datetime
) -> Report:
    name = data.host.hostname.lower()
    host = session.scalar(select(Host).where(Host.name == name))
    if host is None:
        host = Host(name=name, first_seen=now, last_seen=now)
        session.add(host)
    host.last_seen = now
    summary = compute_summary(data.results)
    report = Report(
        host=host,
        token_id=token_id,
        sha256=sha256,
        generated_at=_when(data.generated_at),
        received_at=now,
        worst=_worst(summary.critical, summary.warning, summary.info),
        critical=summary.critical,
        warning=summary.warning,
        info=summary.info,
        error=summary.error,
        os_id=data.host.os_id,
        os_version=data.host.os_version,
        codename=data.host.codename,
        kernel=data.host.kernel,
        tool_version=data.tool.get("version", ""),
        config_stale=data.config is not None and data.config.stale,
        body=text,
    )
    for result in data.results:
        for finding in all_findings(result):
            report.findings.append(
                Finding(check_id=result.id, severity=finding.severity.value, title=finding.title)
            )
    session.add(report)
    _used(session, token_id, now)
    session.commit()
    return report


def _used(session: Session, token_id: int, now: datetime) -> None:
    session.execute(update(Token).where(Token.id == token_id).values(last_used_at=now))
