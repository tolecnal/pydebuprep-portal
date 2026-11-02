"""snapshots: what each report was compared with, and changes since

Revision ID: 0005
Revises: 0004
"""

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

BATCH = 500


def _when(text: Any) -> datetime | None:
    """As ingest reads it: ISO 8601, UTC when no zone is given; None if it isn't."""
    try:
        when = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _snapshot(body: str) -> tuple[dict[str, Any] | None, list[bool]] | None:
    """A stored report's snapshot, and for each finding in the order they were stored
    (each result's findings, then its baseline differences) whether it is a change since
    that snapshot; None if unreadable."""
    try:
        data = json.loads(body)
        flags = [
            since
            for r in data.get("results") or []
            for since, items in ((False, r.get("findings")), (True, r.get("baseline_diff")))
            for _ in items or []
        ]
        baseline = data.get("baseline")
        return (baseline if isinstance(baseline, dict) else None), flags
    except (ValueError, AttributeError, TypeError):
        return None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("baseline", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("reports", sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reports", sa.Column("baseline_os_version", sa.Text(), nullable=True))
    op.add_column("reports", sa.Column("baseline_host", sa.Text(), nullable=True))
    op.add_column(
        "reports", sa.Column("changes", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column(
        "findings",
        sa.Column("since_baseline", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    conn = op.get_bind()
    last = 0
    while True:
        reports = conn.execute(
            sa.text("SELECT id, body FROM reports WHERE id > :last ORDER BY id LIMIT :n"),
            {"last": last, "n": BATCH},
        ).all()
        if not reports:
            break
        for report_id, body in reports:
            last = report_id
            found = _snapshot(body)
            if found is None:
                continue
            baseline, flags = found
            ids = (
                conn.execute(
                    sa.text("SELECT id FROM findings WHERE report_id = :r ORDER BY id"),
                    {"r": report_id},
                )
                .scalars()
                .all()
            )
            changed = [i for i, since in zip(ids, flags, strict=False) if since]
            if len(ids) != len(flags):
                changed = []  # not what was stored: leave every finding a problem now
            if baseline is not None:
                conn.execute(
                    sa.text(
                        "UPDATE reports SET baseline = true, baseline_at = :at, "
                        "baseline_os_version = :os, baseline_host = :host, changes = :n "
                        "WHERE id = :r"
                    ),
                    {
                        "at": _when(baseline.get("created_at")),
                        "os": str(baseline.get("os_version") or "") or None,
                        "host": str(baseline.get("hostname") or "") or None,
                        "n": len(changed),
                        "r": report_id,
                    },
                )
            for finding_id in changed:
                conn.execute(
                    sa.text("UPDATE findings SET since_baseline = true WHERE id = :i"),
                    {"i": finding_id},
                )


def downgrade() -> None:
    op.drop_column("findings", "since_baseline")
    for column in ("changes", "baseline_host", "baseline_os_version", "baseline_at", "baseline"):
        op.drop_column("reports", column)
