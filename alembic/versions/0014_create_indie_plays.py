"""create indie_plays (most clicked games by independent creators)

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indie_plays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("indie_id", sa.Integer(), sa.ForeignKey("indie_games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visitor", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_indie_plays_game_at", "indie_plays", ["indie_id", "at"])
    op.create_index("ix_indie_plays_at", "indie_plays", ["at"])


def downgrade() -> None:
    op.drop_index("ix_indie_plays_at", "indie_plays")
    op.drop_index("ix_indie_plays_game_at", "indie_plays")
    op.drop_table("indie_plays")
