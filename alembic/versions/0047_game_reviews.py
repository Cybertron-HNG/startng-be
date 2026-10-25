"""game review (F2): game_reviews, an admin's look at a new game before it's listed

Revision ID: 0047
Revises: 0046
"""
from alembic import op
import sqlalchemy as sa

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_reviews",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(30), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
    )
    op.create_index("ix_game_reviews_status", "game_reviews", ["status"])
    op.create_index("ix_game_reviews_submitted_at", "game_reviews", ["submitted_at"])


def downgrade() -> None:
    op.drop_table("game_reviews")
