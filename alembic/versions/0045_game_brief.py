"""add games.brief (what a game's repo says about it, read when it is published)

Revision ID: 0045
Revises: 0044
"""
from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("brief", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "brief")
