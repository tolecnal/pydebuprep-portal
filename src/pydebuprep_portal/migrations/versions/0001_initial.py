"""initial

Revision ID: 0001
Revises: none
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hosts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hosts")),
        sa.UniqueConstraint("name", name=op.f("uq_hosts_name")),
    )
    op.create_table(
        "tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('global', 'team', 'host')", name=op.f("ck_tokens_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tokens")),
        sa.UniqueConstraint("public_id", name=op.f("uq_tokens_public_id")),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("host_id", sa.BigInteger(), nullable=False),
        sa.Column("token_id", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worst", sa.Text(), nullable=False),
        sa.Column("critical", sa.Integer(), nullable=False),
        sa.Column("warning", sa.Integer(), nullable=False),
        sa.Column("info", sa.Integer(), nullable=False),
        sa.Column("error", sa.Integer(), nullable=False),
        sa.Column("os_id", sa.Text(), nullable=False),
        sa.Column("os_version", sa.Text(), nullable=False),
        sa.Column("codename", sa.Text(), nullable=False),
        sa.Column("kernel", sa.Text(), nullable=False),
        sa.Column("tool_version", sa.Text(), nullable=False),
        sa.Column("config_stale", sa.Boolean(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "worst IN ('critical', 'warning', 'info', 'ok')", name=op.f("ck_reports_worst")
        ),
        sa.ForeignKeyConstraint(["host_id"], ["hosts.id"], name=op.f("fk_reports_host_id_hosts")),
        sa.ForeignKeyConstraint(
            ["token_id"], ["tokens.id"], name=op.f("fk_reports_token_id_tokens")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint("sha256", name=op.f("uq_reports_sha256")),
    )
    op.create_index(
        "ix_reports_host_id_generated_at", "reports", ["host_id", "generated_at"], unique=False
    )
    op.create_index("ix_reports_received_at", "reports", ["received_at"], unique=False)
    op.create_table(
        "findings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("check_id", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_findings_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )
    op.create_index(
        "ix_findings_check_id_severity", "findings", ["check_id", "severity"], unique=False
    )
    op.create_index(op.f("ix_findings_report_id"), "findings", ["report_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_findings_report_id"), table_name="findings")
    op.drop_index("ix_findings_check_id_severity", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_reports_received_at", table_name="reports")
    op.drop_index("ix_reports_host_id_generated_at", table_name="reports")
    op.drop_table("reports")
    op.drop_table("tokens")
    op.drop_table("hosts")
