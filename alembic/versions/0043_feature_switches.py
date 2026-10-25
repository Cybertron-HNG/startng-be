"""feature_switches: features an admin switched on or off in the admin page

Revision ID: 0043
Revises: 0042
"""
from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_switches",
        sa.Column("feature", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(255), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("feature_switches")
