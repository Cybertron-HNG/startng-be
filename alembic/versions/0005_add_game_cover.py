"""add game cover

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("cover", sa.String(200)))
    op.add_column("games", sa.Column("cover_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("games", "cover_at")
    op.drop_column("games", "cover")
