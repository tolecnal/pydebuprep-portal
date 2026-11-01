"""machine ids and finding text

Revision ID: 0004
Revises: 0003
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

BATCH = 500


def _texts(body: str) -> list[str] | None:
    """The search text of each finding in a stored report, in the order they were stored
    (each result's findings, then its baseline differences); None if unreadable."""
    try:
        results = json.loads(body).get("results") or []
        return [
            "\n".join(line for line in (f.get("detail") or "", *(f.get("evidence") or [])) if line)
            for r in results
            for f in [*(r.get("findings") or []), *(r.get("baseline_diff") or [])]
        ]
    except (ValueError, AttributeError, TypeError):
        return None


def upgrade() -> None:
    op.add_column("reports", sa.Column("machine_id", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("text", sa.Text(), server_default="", nullable=False))
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
            texts = _texts(body)
            ids = (
                conn.execute(
                    sa.text("SELECT id FROM findings WHERE report_id = :r ORDER BY id"),
                    {"r": report_id},
                )
                .scalars()
                .all()
            )
            if texts is None or len(texts) != len(ids):
                continue  # not what was stored: leave its findings searchable by title
            for finding_id, text in zip(ids, texts, strict=True):
                if text:
                    conn.execute(
                        sa.text("UPDATE findings SET text = :t WHERE id = :i"),
                        {"t": text, "i": finding_id},
                    )


def downgrade() -> None:
    op.drop_column("findings", "text")
    op.drop_column("reports", "machine_id")
