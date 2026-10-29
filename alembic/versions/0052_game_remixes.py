"""remix (Ziki #83): game_remixes, whether a game may be remixed and which game it remixes

Revision ID: 0052
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0052"
down_revision = "0049"  # renumber right before merging if 0050 or 0051 landed first
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_remixes",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("remix_of", sa.String(40), nullable=True),
    )
    op.create_index("ix_game_remixes_remix_of", "game_remixes", ["remix_of"])


def downgrade() -> None:
    op.drop_table("game_remixes")
