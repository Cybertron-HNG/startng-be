"""daily tournaments: tournaments, their games, entries, scores and points; games.meets_standards

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("meets_standards", sa.Boolean(), nullable=True))
    op.create_table(
        "tournaments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False, unique=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("winner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_tournaments_ends_at", "tournaments", ["ends_at"])
    op.create_table(
        "tournament_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tournament_id", "game_name", name="uq_tournament_games"),
    )
    op.create_index("ix_tournament_games_tournament_id", "tournament_games", ["tournament_id"])
    op.create_index("ix_tournament_games_game_name", "tournament_games", ["game_name"])
    op.create_table(
        "tournament_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tournament_id", "user_id", name="uq_tournament_entries"),
    )
    op.create_index("ix_tournament_entries_tournament_id", "tournament_entries", ["tournament_id"])
    op.create_index("ix_tournament_entries_user_id", "tournament_entries", ["user_id"])
    op.create_table(
        "tournament_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("best", sa.BigInteger(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tournament_id", "game_name", "user_id", name="uq_tournament_scores"),
    )
    op.create_index("ix_tournament_scores_tournament_id", "tournament_scores", ["tournament_id"])
    op.create_table(
        "tournament_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("firsts", sa.Integer(), nullable=False),
        sa.Column("place", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tournament_id", "user_id", name="uq_tournament_points"),
    )
    op.create_index("ix_tournament_points_tournament_id", "tournament_points", ["tournament_id"])
    op.create_index("ix_tournament_points_user_id", "tournament_points", ["user_id"])


def downgrade() -> None:
    op.drop_table("tournament_points")
    op.drop_table("tournament_scores")
    op.drop_table("tournament_entries")
    op.drop_table("tournament_games")
    op.drop_index("ix_tournaments_ends_at", table_name="tournaments")
    op.drop_table("tournaments")
    op.drop_column("games", "meets_standards")
