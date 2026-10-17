"""create indie_games (Indie Spotlight)

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
        "indie_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("url", sa.String(300), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cover", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_indie_games_owner_id", "indie_games", ["owner_id"])
    op.create_index("ix_indie_games_created_at", "indie_games", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_indie_games_created_at", "indie_games")
    op.drop_index("ix_indie_games_owner_id", "indie_games")
    op.drop_table("indie_games")
