"""player_pulse (Ziki #58): pulse_votes, players' "how was that?" answers after a round

Revision ID: 0050
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pulse_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("feel", sa.String(12), nullable=False),
        sa.Column("note", sa.String(140), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "game_name", "day", name="uq_pulse_votes_user_game_day"),
    )
    op.create_index("ix_pulse_votes_user_id", "pulse_votes", ["user_id"])
    op.create_index("ix_pulse_votes_game_name", "pulse_votes", ["game_name"])
    op.create_index("ix_pulse_votes_created_at", "pulse_votes", ["created_at"])


def downgrade() -> None:
    op.drop_table("pulse_votes")
