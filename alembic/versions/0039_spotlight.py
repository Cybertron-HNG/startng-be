"""spotlight (A2): game_of_the_week, daily_challenges and daily_challenge_completions

Revision ID: 0039
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_of_the_week",
        sa.Column("week_start", sa.Date, primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("picked_by", sa.String(255), nullable=False),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_game_of_the_week_game_name", "game_of_the_week", ["game_name"])
    op.create_table(
        "daily_challenges",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("stat_key", sa.String(24), nullable=False),
        sa.Column("goal", sa.Integer, nullable=False),
        sa.Column("runs_seen", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "daily_challenge_completions",
        sa.Column("day", sa.Date, sa.ForeignKey("daily_challenges.day", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("run_id", sa.Integer),
        sa.Column("value", sa.Integer, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_daily_challenge_completions_user_id", "daily_challenge_completions", ["user_id"])


def downgrade() -> None:
    op.drop_table("daily_challenge_completions")
    op.drop_table("daily_challenges")
    op.drop_table("game_of_the_week")
