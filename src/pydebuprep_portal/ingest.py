"""Reading and storing uploaded reports."""

from __future__ import annotations

import hashlib
import json
import zlib
from datetime import UTC, datetime
from functools import cache

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydebuprep.model import Report as ReportData
from pydebuprep.model import Severity, all_findings, compute_summary
from pydebuprep.reportfile import ReportFileError, load_report
from pydebuprep.schema import report_schema
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect, Request

from pydebuprep_portal.audit import record
from pydebuprep_portal.mapping import move_reports
from pydebuprep_portal.models import Finding, Host, Report, Team, Token
from pydebuprep_portal.settings_store import get_settings

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
    except ClientDisconnect:
        raise IngestError(400, "the upload was cut off") from None
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
        problem = schema_problem(json.loads(text))
    except ReportFileError as exc:
        raise IngestError(422, f"not a pydebuprep report: {exc}") from None
    except RecursionError:
        raise IngestError(422, "the report is nested too deeply") from None
    if problem:
        raise IngestError(422, f"does not match the report schema: {problem}")
    hostname = report.host.hostname
    if not valid_host_name(hostname):
        raise IngestError(422, f"host.hostname is not a host name: {hostname!r}")
    if _has_nul(report.to_dict()):
        raise IngestError(422, "the report contains a NUL character, which can't be stored")
    return report


@cache
def _report_validator() -> Draft202012Validator:
    return Draft202012Validator(report_schema())


def schema_problem(data: object) -> str | None:
    """The first way DATA departs from pydebuprep's published report schema, or None."""
    error = best_match(_report_validator().iter_errors(data))
    if error is None:
        return None
    where = ".".join(str(part) for part in error.absolute_path) or "report"
    return f"{where}: {error.message}"[:300]


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
    token = session.get(Token, token_id)
    if token is None:  # pragma: no cover - authenticated a moment ago
        raise IngestError(401, "unknown upload token")
    if token.revoked_at is not None or (token.expires_at is not None and token.expires_at <= now):
        # Revoked or expired while the body arrived.
        raise IngestError(401, "this upload token is no longer valid")
    if token.kind == "host" and token.host_name != data.host.hostname.lower():
        # Before the duplicate check: a host token can't resend another host's report.
        raise IngestError(403, f"this token is for host {token.host_name} only")
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
                session, data=data, text=body.decode(), sha256=sha256, token=token, now=now
            )
        except IntegrityError:
            session.rollback()
        else:
            return report, True
    raise IngestError(503, "the report could not be stored just now; send it again")


def _insert(
    session: Session, *, data: ReportData, text: str, sha256: str, token: Token, now: datetime
) -> Report:
    name = data.host.hostname.lower()
    # Locked until this report is stored, so a host being mapped meanwhile can't leave
    # the report behind in the queue: whoever locks the host second sees the first's work.
    host = session.scalar(select(Host).where(Host.name == name).with_for_update())
    new = host is None
    if host is None:
        host = Host(name=name, first_seen=now, last_seen=now)
        session.add(host)
        session.flush()  # a host created concurrently fails here and is retried
    host.last_seen = now
    team_id, token_team_id = _assign(session, host, token, new=new)
    summary = compute_summary(data.results)
    report = Report(
        host=host,
        token_id=token.id,
        team_id=team_id,
        token_team_id=token_team_id,
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
        machine_id=data.host.machine_id,
    )
    for result in data.results:
        for finding in all_findings(result):
            report.findings.append(
                Finding(
                    check_id=result.id,
                    severity=finding.severity.value,
                    title=finding.title,
                    text=finding_text(finding.detail, finding.evidence),
                )
            )
    session.add(report)
    _used(session, token.id, now)
    session.commit()
    return report


def finding_text(detail: str | None, evidence: list[str]) -> str:
    """What a finding's search looks at besides its title."""
    return "\n".join(line for line in (detail or "", *evidence) if line)


def _assign(
    session: Session, host: Host, token: Token, *, new: bool
) -> tuple[int | None, int | None]:
    """(team, flag) of a new report of HOST sent with TOKEN. A mapped host's reports go to
    its team, flagged when another team's token sent them. With a team or host token, a
    new host joins the token's team at once; a host waiting in the queue does too, with
    its earlier reports, unless only admins map hosts: then the report waits in the queue,
    flagged with the token's team. With a global token, an unmapped host stays queued."""
    if host.team_id is not None:
        foreign = token.team_id is not None and token.team_id != host.team_id
        return host.team_id, token.team_id if foreign else None
    if token.team_id is None:
        return None, None
    if not new and get_settings(session).only_admins_map:
        return None, token.team_id
    moved = move_reports(session, host, token.team_id)
    team = session.get(Team, token.team_id)
    record(session, f"token {token.name}", "host.map", host.name,
           team=team.name if team else None, how="first upload", reports_moved=moved)  # fmt: skip
    return token.team_id, None


def _used(session: Session, token_id: int, now: datetime) -> None:
    session.execute(update(Token).where(Token.id == token_id).values(last_used_at=now))
