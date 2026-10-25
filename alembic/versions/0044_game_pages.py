"""game_pages: the about, how to play, FAQ and walkthrough pages of games (draft, approved or skipped)

Revision ID: 0044
Revises: 0043
"""
from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("title", sa.String(160), nullable=False, server_default=""),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.String(200)),
        sa.Column("facts", sa.JSON()),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.UniqueConstraint("game_name", "kind", name="uq_game_pages"),
    )
    op.create_index("ix_game_pages_game_name", "game_pages", ["game_name"])
    op.create_index("ix_game_pages_status", "game_pages", ["status"])


def downgrade() -> None:
    op.drop_table("game_pages")
