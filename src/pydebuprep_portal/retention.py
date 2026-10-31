"""Retention: a global TTL with team and host overrides (host beats team beats global),
expired daily inside the service. The newest report of each host is never expired."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from pydebuprep_portal.audit import record
from pydebuprep_portal.models import Host, Team
from pydebuprep_portal.settings_store import GlobalSettings, get_settings

log = logging.getLogger(__name__)

# Held for one expiry run, so two portal replicas never run it at the same time.
LOCK_KEY = 0x7064707265746E  # "pdpretn"
DAY = 86400.0

# A report is expired when it was received more than its host's effective retention ago
# and is not the newest (by generated time) report of its host.
EXPIRE_SQL = text(
    """
    WITH ranked AS (
        SELECT id, row_number() OVER (
            PARTITION BY host_id ORDER BY generated_at DESC, id DESC) AS rank
        FROM reports
    ),
    days AS (
        SELECT h.id AS host_id,
               CASE WHEN h.keep_forever THEN NULL
                    WHEN h.retention_days IS NOT NULL THEN h.retention_days
                    WHEN t.keep_forever THEN NULL
                    WHEN t.retention_days IS NOT NULL THEN t.retention_days
                    ELSE CAST(:global_days AS integer) END AS days
        FROM hosts h LEFT JOIN teams t ON t.id = h.team_id
    )
    DELETE FROM reports r
    USING ranked k, days d
    WHERE k.id = r.id AND k.rank > 1 AND d.host_id = r.host_id AND d.days IS NOT NULL
      AND r.received_at < :now - make_interval(days => d.days)
    RETURNING r.id
    """
)


@dataclass(frozen=True)
class Retention:
    days: int | None  # None: keep forever
    source: Literal["host", "team", "global"]


def effective_retention(host: Host, team: Team | None, settings: GlobalSettings) -> Retention:
    if host.keep_forever or host.retention_days is not None:
        return Retention(None if host.keep_forever else host.retention_days, "host")
    if team is not None and (team.keep_forever or team.retention_days is not None):
        return Retention(None if team.keep_forever else team.retention_days, "team")
    return Retention(settings.retention_days, "global")


def team_retention(team: Team, settings: GlobalSettings) -> Retention:
    """A team's own retention, or the global one it inherits."""
    return effective_retention(Host(keep_forever=False), team, settings)


def describe(retention: Retention) -> str:
    amount = "forever" if retention.days is None else f"{retention.days} days"
    return f"{amount} ({retention.source})"


def expire_reports(session: Session, now: datetime) -> int:
    """Delete expired reports; their number. Does nothing while another run holds the lock."""
    if not session.scalar(select(func.pg_try_advisory_xact_lock(LOCK_KEY))):
        session.rollback()
        return 0
    global_days = get_settings(session).retention_days
    deleted = len(session.execute(EXPIRE_SQL, {"global_days": global_days, "now": now}).all())
    if deleted:
        record(session, "retention", "retention.expire", "reports", deleted=deleted)
    session.commit()  # also releases the lock
    return deleted


def _expire_once(maker: sessionmaker[Session], expire: Callable[[Session, datetime], int]) -> int:
    with maker() as session:
        return expire(session, datetime.now(UTC))


async def run_expiry_forever(
    maker: sessionmaker[Session],
    *,
    first_delay: float = 60.0,
    interval: float = DAY,
    expire: Callable[[Session, datetime], int] = expire_reports,
) -> None:
    """Expire reports FIRST_DELAY seconds after start, then every INTERVAL seconds; a
    failed run is logged and the next one runs as planned."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            deleted = await run_in_threadpool(_expire_once, maker, expire)
            if deleted:
                log.info("expired %d reports", deleted)
        except Exception:
            log.exception("expiring reports failed; trying again in %s seconds", interval)
        await asyncio.sleep(interval)
