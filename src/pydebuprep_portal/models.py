"""The portal's tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pydebuprep_portal.db import Base

WORST = ("critical", "warning", "info", "ok")
TOKEN_KINDS = ("global", "team", "host")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


DEFAULT_RETENTION_DAYS = 365
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint("retention_days IS NULL OR retention_days >= 1", name="retention_days"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    public: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    keep_forever: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text)


class Host(Base):
    __tablename__ = "hosts"
    __table_args__ = (
        CheckConstraint("retention_days IS NULL OR retention_days >= 1", name="retention_days"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    retention_days: Mapped[int | None] = mapped_column(Integer)
    keep_forever: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    team: Mapped[Team | None] = relationship()


class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = (
        CheckConstraint(_in("kind", TOKEN_KINDS), name="kind"),
        CheckConstraint("(kind = 'global') = (team_id IS NULL)", name="team_kind"),
        CheckConstraint("(kind = 'host') = (host_name IS NOT NULL)", name="host_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    public_id: Mapped[str] = mapped_column(Text, unique=True)
    secret_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    host_name: Mapped[str | None] = mapped_column(Text)

    team: Mapped[Team | None] = relationship()


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(_in("worst", WORST), name="worst"),
        Index("ix_reports_host_id_generated_at", "host_id", "generated_at"),
        Index("ix_reports_received_at", "received_at"),
        Index("ix_reports_team_id_id", "team_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id"))
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    sha256: Mapped[str] = mapped_column(Text, unique=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    worst: Mapped[str] = mapped_column(Text)
    critical: Mapped[int] = mapped_column(Integer)
    warning: Mapped[int] = mapped_column(Integer)
    info: Mapped[int] = mapped_column(Integer)
    error: Mapped[int] = mapped_column(Integer)
    os_id: Mapped[str] = mapped_column(Text)
    os_version: Mapped[str] = mapped_column(Text)
    codename: Mapped[str] = mapped_column(Text)
    kernel: Mapped[str] = mapped_column(Text)
    tool_version: Mapped[str] = mapped_column(Text)
    config_stale: Mapped[bool]
    body: Mapped[str] = mapped_column(Text)
    # NULL: the unassigned queue.
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    # Set when the report was sent with another team's token (a flag for admins).
    token_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))

    host: Mapped[Host] = relationship()
    token: Mapped[Token] = relationship()
    team: Mapped[Team | None] = relationship(foreign_keys=[team_id])
    token_team: Mapped[Team | None] = relationship(foreign_keys=[token_team_id])
    findings: Mapped[list[Finding]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_check_id_severity", "check_id", "severity"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)

    report: Mapped[Report] = relationship(back_populates="findings")


class Setting(Base):
    """The admins' global settings: a single row."""

    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("retention_days IS NULL OR retention_days >= 1", name="retention_days"),
        CheckConstraint("max_upload_bytes > 0", name="max_upload_bytes"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL: keep reports forever.
    retention_days: Mapped[int | None] = mapped_column(Integer)
    only_admins_map: Mapped[bool] = mapped_column(Boolean)
    max_upload_bytes: Mapped[int] = mapped_column(Integer)


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
