"""profiles (S1): profile_settings, a player's choices about their page

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_settings",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("public", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("show_favorites", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("show_ranks", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("notice_seen", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("profile_settings")
