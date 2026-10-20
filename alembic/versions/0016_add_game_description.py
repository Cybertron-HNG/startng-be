"""add games.description (one line about the game, for its page on start.ng)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("description", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "description")
