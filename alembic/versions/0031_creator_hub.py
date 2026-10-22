"""creator hub (T2): game_daily_stats (the nightly rollup) and creator_profiles (bios)

Revision ID: 0031
Revises: 0030
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_daily_stats",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("plays", sa.Integer, nullable=False, server_default="0"),
        sa.Column("visitors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("runs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("players", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_game_daily_stats_day", "game_daily_stats", ["day"])
    op.create_table(
        "creator_profiles",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("bio", sa.String(200)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("creator_profiles")
    op.drop_table("game_daily_stats")
