"""create pending_games

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
    op.create_table(
        "pending_games",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("repo_url", sa.String(300), nullable=False),
        sa.Column("branch", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pending_games_repo_url", "pending_games", ["repo_url"])
    op.create_index("ix_pending_games_expires_at", "pending_games", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_pending_games_expires_at", table_name="pending_games")
    op.drop_index("ix_pending_games_repo_url", table_name="pending_games")
    op.drop_table("pending_games")
