"""leagues: rounds, members, groups and entries for the three league tracks (app/features/leagues)

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "league_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track", sa.String(16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("track", "starts_at", name="uq_league_rounds_track_start"),
    )
    op.create_index("ix_league_rounds_ends_at", "league_rounds", ["ends_at"])
    op.create_table(
        "league_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track", sa.String(16), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("last_round_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "track", name="uq_league_members_user_track"),
    )
    op.create_table(
        "league_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("round_id", sa.Integer(), sa.ForeignKey("league_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.UniqueConstraint("round_id", "tier", "number", name="uq_league_groups_round_tier_number"),
    )
    op.create_index("ix_league_groups_round_id", "league_groups", ["round_id"])
    op.create_table(
        "league_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("round_id", sa.Integer(), sa.ForeignKey("league_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("league_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("place", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(8), nullable=True),
        sa.UniqueConstraint("round_id", "user_id", name="uq_league_entries_round_user"),
        sa.UniqueConstraint("group_id", "seat", name="uq_league_entries_group_seat"),
    )
    op.create_index("ix_league_entries_round_id", "league_entries", ["round_id"])
    op.create_index("ix_league_entries_group_id", "league_entries", ["group_id"])
    op.create_index("ix_league_entries_user_id", "league_entries", ["user_id"])


def downgrade() -> None:
    op.drop_table("league_entries")
    op.drop_table("league_groups")
    op.drop_table("league_members")
    op.drop_table("league_rounds")
