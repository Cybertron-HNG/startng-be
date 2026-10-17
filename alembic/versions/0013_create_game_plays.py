"""create game_plays (most played games)

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_plays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("visitor", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_game_plays_game_at", "game_plays", ["game_name", "at"])
    op.create_index("ix_game_plays_at", "game_plays", ["at"])


def downgrade() -> None:
    op.drop_index("ix_game_plays_at", "game_plays")
    op.drop_index("ix_game_plays_game_at", "game_plays")
    op.drop_table("game_plays")
