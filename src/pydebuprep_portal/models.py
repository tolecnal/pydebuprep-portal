"""The portal's tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pydebuprep_portal.db import Base

WORST = ("critical", "warning", "info", "ok")
TOKEN_KINDS = ("global", "team", "host")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = (CheckConstraint(_in("kind", TOKEN_KINDS), name="kind"),)

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


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(_in("worst", WORST), name="worst"),
        Index("ix_reports_host_id_generated_at", "host_id", "generated_at"),
        Index("ix_reports_received_at", "received_at"),
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

    host: Mapped[Host] = relationship()
    token: Mapped[Token] = relationship()
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
