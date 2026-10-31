"""teams

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], unique=False)
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("only_admins_map", sa.Boolean(), nullable=False),
        sa.Column("max_upload_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_settings_single_row")),
        sa.CheckConstraint("max_upload_bytes > 0", name=op.f("ck_settings_max_upload_bytes")),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days >= 1", name=op.f("ck_settings_retention_days")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings")),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("keep_forever", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days >= 1", name=op.f("ck_teams_retention_days")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )
    op.add_column("hosts", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.add_column("hosts", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.add_column(
        "hosts",
        sa.Column("keep_forever", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(op.f("ix_hosts_team_id"), "hosts", ["team_id"], unique=False)
    op.create_foreign_key(op.f("fk_hosts_team_id_teams"), "hosts", "teams", ["team_id"], ["id"])
    op.add_column("reports", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.add_column("reports", sa.Column("token_team_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_reports_team_id_id", "reports", ["team_id", "id"], unique=False)
    op.create_foreign_key(op.f("fk_reports_team_id_teams"), "reports", "teams", ["team_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_reports_token_team_id_teams"), "reports", "teams", ["token_team_id"], ["id"]
    )
    op.add_column("tokens", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.add_column("tokens", sa.Column("host_name", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_tokens_team_id_teams"), "tokens", "teams", ["team_id"], ["id"])
    # Autogenerate doesn't see check constraints on existing tables.
    op.create_check_constraint(
        op.f("ck_hosts_retention_days"), "hosts", "retention_days IS NULL OR retention_days >= 1"
    )
    op.create_check_constraint(
        op.f("ck_tokens_team_kind"), "tokens", "(kind = 'global') = (team_id IS NULL)"
    )
    op.create_check_constraint(
        op.f("ck_tokens_host_kind"), "tokens", "(kind = 'host') = (host_name IS NOT NULL)"
    )
    op.execute(
        "INSERT INTO settings (id, retention_days, only_admins_map, max_upload_bytes)"
        " VALUES (1, 365, false, 10485760)"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tokens_host_kind"), "tokens", type_="check")
    op.drop_constraint(op.f("ck_tokens_team_kind"), "tokens", type_="check")
    op.drop_constraint(op.f("ck_hosts_retention_days"), "hosts", type_="check")
    op.drop_constraint(op.f("fk_tokens_team_id_teams"), "tokens", type_="foreignkey")
    op.drop_column("tokens", "host_name")
    op.drop_column("tokens", "team_id")
    op.drop_constraint(op.f("fk_reports_token_team_id_teams"), "reports", type_="foreignkey")
    op.drop_constraint(op.f("fk_reports_team_id_teams"), "reports", type_="foreignkey")
    op.drop_index("ix_reports_team_id_id", table_name="reports")
    op.drop_column("reports", "token_team_id")
    op.drop_column("reports", "team_id")
    op.drop_constraint(op.f("fk_hosts_team_id_teams"), "hosts", type_="foreignkey")
    op.drop_index(op.f("ix_hosts_team_id"), table_name="hosts")
    op.drop_column("hosts", "keep_forever")
    op.drop_column("hosts", "retention_days")
    op.drop_column("hosts", "team_id")
    op.drop_table("teams")
    op.drop_table("settings")
    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log")
    op.drop_table("audit_log")
