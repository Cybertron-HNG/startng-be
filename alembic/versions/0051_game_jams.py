"""jams (Ziki #35): jams, jam_entries, jam_votes

Revision ID: 0051
Revises: 0049
(Open PRs already take 0050: renumber right before merging, as app/features/_template/models.py says.)
"""
from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("brief", sa.String(400), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entries_close_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voting_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "jam_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jam_id", sa.Integer(), sa.ForeignKey("jams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("jam_id", "game_name", name="uq_jam_entries"),
    )
    op.create_index("ix_jam_entries_jam_id", "jam_entries", ["jam_id"])
    op.create_table(
        "jam_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jam_id", sa.Integer(), sa.ForeignKey("jams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("jam_id", "game_name", "user_id", name="uq_jam_votes"),
    )
    op.create_index("ix_jam_votes_jam_id", "jam_votes", ["jam_id"])


def downgrade() -> None:
    op.drop_table("jam_votes")
    op.drop_table("jam_entries")
    op.drop_table("jams")
