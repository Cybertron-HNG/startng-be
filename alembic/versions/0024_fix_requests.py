"""needs_fixing (H2): fix_requests, games an admin marked as needing a fix

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fix_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_fixed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(255), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_fix_requests_game_name", "fix_requests", ["game_name"])
    op.create_index("ix_fix_requests_status", "fix_requests", ["status"])


def downgrade() -> None:
    op.drop_table("fix_requests")
