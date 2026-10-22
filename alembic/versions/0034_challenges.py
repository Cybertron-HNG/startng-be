"""challenges (B1): challenges and challenge_attempts

Revision ID: 0034
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "challenges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("challenger_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("score", sa.BigInteger, nullable=False),
        sa.Column("stats", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_challenges_challenger_game", "challenges", ["challenger_id", "game_name"])
    op.create_index("ix_challenges_expires_at", "challenges", ["expires_at"])
    op.create_table(
        "challenge_attempts",
        sa.Column("challenge_id", sa.Integer, sa.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_score", sa.BigInteger),
        sa.Column("best_stats", sa.JSON),
        sa.Column("beaten_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_challenge_attempts_user_id", "challenge_attempts", ["user_id"])


def downgrade() -> None:
    op.drop_table("challenge_attempts")
    op.drop_table("challenges")
