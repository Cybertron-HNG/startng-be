"""private tournaments made by admins (F9): private_tournaments.by_admin and max_members

Revision ID: 0048
Revises: 0047
"""
from alembic import op
import sqlalchemy as sa

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("private_tournaments", sa.Column("by_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("private_tournaments", sa.Column("max_members", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("private_tournaments") as batch:
        batch.drop_column("max_members")
        batch.drop_column("by_admin")
