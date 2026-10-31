"""configs

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_configs_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_configs")),
        sa.UniqueConstraint("team_id", "name", name="uq_configs_team_id_name"),
    )
    op.create_table(
        "config_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("config_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_config_versions_version")),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["configs.id"],
            name=op.f("fk_config_versions_config_id_configs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_versions")),
        sa.UniqueConstraint("config_id", "version", name="uq_config_versions_config_id_version"),
    )


def downgrade() -> None:
    op.drop_table("config_versions")
    op.drop_table("configs")
