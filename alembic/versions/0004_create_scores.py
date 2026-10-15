"""create scores

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("best", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "game_name", name="uq_scores_user_game"),
    )
    # The leaderboard reads a game's scores, highest first.
    op.create_index("ix_scores_game_best", "scores", ["game_name", "best"])


def downgrade() -> None:
    op.drop_index("ix_scores_game_best", table_name="scores")
    op.drop_table("scores")
