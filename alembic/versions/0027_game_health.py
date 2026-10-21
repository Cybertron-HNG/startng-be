"""game health (H2): game_health, the latest health check of each live game

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_health",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("problems", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unhealthy_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_game_health_status", "game_health", ["status"])
    op.create_index("ix_game_health_checked_at", "game_health", ["checked_at"])


def downgrade() -> None:
    op.drop_table("game_health")
