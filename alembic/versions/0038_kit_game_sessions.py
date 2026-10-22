"""kit: game_sessions, how long people play games that use kit.js (app/features/kit)

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False, unique=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kit_version", sa.String(16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_game_sessions_game_started", "game_sessions", ["game_name", "started_at"])
    op.create_index("ix_game_sessions_user_id", "game_sessions", ["user_id"])
    op.create_index("ix_game_sessions_started_at", "game_sessions", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_game_sessions_started_at", table_name="game_sessions")
    op.drop_index("ix_game_sessions_user_id", table_name="game_sessions")
    op.drop_index("ix_game_sessions_game_started", table_name="game_sessions")
    op.drop_table("game_sessions")
