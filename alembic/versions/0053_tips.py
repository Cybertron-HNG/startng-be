"""tips (Ziki #82): tips, players' coins to the creators of games they played

Revision ID: 0053
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0053"
down_revision = "0049"  # renumber right before merging if 0050 to 0052 landed first
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(140), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tips_from_day", "tips", ["from_user_id", "day"])
    op.create_index("ix_tips_to_user_id", "tips", ["to_user_id"])
    op.create_index("ix_tips_game_name", "tips", ["game_name"])


def downgrade() -> None:
    op.drop_table("tips")
