"""add game update_problem

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("update_problem", sa.JSON()))


def downgrade() -> None:
    op.drop_column("games", "update_problem")
