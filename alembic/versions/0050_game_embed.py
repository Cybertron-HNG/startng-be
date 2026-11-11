"""embed (Issue #85): games.allow_embed and game_plays.embed

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
    op.add_column("games", sa.Column("allow_embed", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("game_plays", sa.Column("embed", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("game_plays", "embed")
    op.drop_column("games", "allow_embed")
