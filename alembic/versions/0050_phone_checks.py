"""phone_check (Ziki #14): phone_checks, the latest phone check of each published game

Revision ID: 0050
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_checks",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("method", sa.String(8), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_phone_checks_status", "phone_checks", ["status"])
    op.create_index("ix_phone_checks_queued_at", "phone_checks", ["queued_at"])


def downgrade() -> None:
    op.drop_table("phone_checks")
