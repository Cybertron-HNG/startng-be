"""tags: tags and game_tags (many tags per game, a person approves each one)

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
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("group_name", sa.String(40), nullable=False),
        sa.Column("blurb", sa.String(160), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("everywhere", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_tags_group_name", "tags", ["group_name"])
    op.create_table(
        "game_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="suggested"),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("game_name", "tag_id", name="uq_game_tags"),
    )
    op.create_index("ix_game_tags_game_name", "game_tags", ["game_name"])
    op.create_index("ix_game_tags_tag_id", "game_tags", ["tag_id"])
    op.create_index("ix_game_tags_status", "game_tags", ["status"])


def downgrade() -> None:
    op.drop_table("game_tags")
    op.drop_table("tags")
