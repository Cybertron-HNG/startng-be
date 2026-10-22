"""score integrity, second drop: flagged runs and the review queue (app/features/integrity)

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("rank_after", sa.Integer(), nullable=True))
    op.create_table(
        "score_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("previous_best", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
    )
    op.create_index("ix_score_flags_user_id", "score_flags", ["user_id"])
    op.create_index("ix_score_flags_game_status", "score_flags", ["game_name", "status"])


def downgrade() -> None:
    op.drop_table("score_flags")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("rank_after")
        batch.drop_column("flagged")
