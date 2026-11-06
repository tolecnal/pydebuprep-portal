"""Signed-in sessions, so signing out ends them on the server too.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_sessions")),
    )
    op.create_index(op.f("ix_login_sessions_expires_at"), "login_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_login_sessions_expires_at"), table_name="login_sessions")
    op.drop_table("login_sessions")
