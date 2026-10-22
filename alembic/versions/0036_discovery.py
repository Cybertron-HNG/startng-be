"""discovery (A1): game_rank, featured_games and game_discovery

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_rank",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("visitors_7", sa.Integer, nullable=False, server_default="0"),
        sa.Column("visitors_prev_7", sa.Integer, nullable=False, server_default="0"),
        sa.Column("runs_7", sa.Integer, nullable=False, server_default="0"),
        sa.Column("players_7", sa.Integer, nullable=False, server_default="0"),
        sa.Column("return_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("avg_session", sa.Integer),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_game_rank_score", "game_rank", ["score"])
    op.create_table(
        "featured_games",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("shelf", sa.String(10), primary_key=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("added_by", sa.String(255), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "game_discovery",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("mobile", sa.Boolean, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("game_discovery")
    op.drop_table("featured_games")
    op.drop_table("game_rank")
