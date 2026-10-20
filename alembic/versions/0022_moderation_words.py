"""moderation (H2 name filter): moderation_words, the words admins block or allow

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moderation_words",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(40), nullable=False, unique=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("strong", sa.Boolean(), nullable=False),
        sa.Column("added_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("moderation_words")
