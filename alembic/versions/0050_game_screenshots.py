"""screenshots (Ziki #19): game_screenshots, the pictures a game's startng.json lists for its page

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
        "game_screenshots",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("game_screenshots")
