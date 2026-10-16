"""add game last_commit, commit_etag, checked_at

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("last_commit", sa.String(40)))
    op.add_column("games", sa.Column("commit_etag", sa.String(200)))
    op.add_column("games", sa.Column("checked_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("games", "checked_at")
    op.drop_column("games", "commit_etag")
    op.drop_column("games", "last_commit")
