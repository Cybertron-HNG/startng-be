"""add game update_problem

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("update_problem", sa.JSON()))


def downgrade() -> None:
    op.drop_column("games", "update_problem")
