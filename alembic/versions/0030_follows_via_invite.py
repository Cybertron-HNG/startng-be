"""follows.via_invite: follows made by accepting an invite link, which don't count toward the daily follow limit

Revision ID: 0030
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("follows") as batch:
        batch.add_column(sa.Column("via_invite", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("follows") as batch:
        batch.drop_column("via_invite")
