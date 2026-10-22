"""email (H1): email_announcements, so each tournament is announced once

Revision ID: 0028
Revises: 0027
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_announcements",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("email_announcements")
