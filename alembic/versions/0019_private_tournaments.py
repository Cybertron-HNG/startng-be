"""private tournaments: friends' own tournaments with an invite link (app/features/private_tournaments)

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "private_tournaments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(60), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_private_tournaments_owner_id", "private_tournaments", ["owner_id"])
    op.create_index("ix_private_tournaments_ends_at", "private_tournaments", ["ends_at"])
    op.create_table(
        "private_tournament_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("private_tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tournament_id", "game_name", name="uq_private_tournament_games"),
    )
    op.create_index("ix_private_tournament_games_tournament_id", "private_tournament_games", ["tournament_id"])
    op.create_index("ix_private_tournament_games_game_name", "private_tournament_games", ["game_name"])
    op.create_table(
        "private_tournament_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("private_tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tournament_id", "user_id", name="uq_private_tournament_members"),
    )
    op.create_index("ix_private_tournament_members_tournament_id", "private_tournament_members", ["tournament_id"])
    op.create_index("ix_private_tournament_members_user_id", "private_tournament_members", ["user_id"])
    op.create_table(
        "private_tournament_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("private_tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("best", sa.BigInteger(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tournament_id", "game_name", "user_id", name="uq_private_tournament_scores"),
    )
    op.create_index("ix_private_tournament_scores_tournament_id", "private_tournament_scores", ["tournament_id"])


def downgrade() -> None:
    op.drop_table("private_tournament_scores")
    op.drop_table("private_tournament_members")
    op.drop_table("private_tournament_games")
    op.drop_table("private_tournaments")
