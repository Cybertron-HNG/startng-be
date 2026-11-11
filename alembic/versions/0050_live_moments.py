"""live (Ziki #37): live_moments, recent moments shown on the homepage (a new #1 on a game)

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
        "live_moments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_live_moments_at", "live_moments", ["at"])


def downgrade() -> None:
    op.drop_table("live_moments")
