"""create explore_games: external games listed on the Explore page

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "explore_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("creator", sa.String(40), nullable=False),
        sa.Column("url", sa.String(300), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("description", sa.String(600), nullable=False),
        sa.Column("cover", sa.LargeBinary(), nullable=False),
        sa.Column("cover_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(10), nullable=False, server_default="approved"),
        sa.Column("manage_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_explore_games_status", "explore_games", ["status"])
    op.create_index("ix_explore_games_created_at", "explore_games", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_explore_games_created_at", table_name="explore_games")
    op.drop_index("ix_explore_games_status", table_name="explore_games")
    op.drop_table("explore_games")
