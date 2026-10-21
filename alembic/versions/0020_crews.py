"""crews: saved groups of players with an invite link (app/features/crews)

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_crews_owner_id", "crews", ["owner_id"])
    op.create_table(
        "crew_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("crew_id", sa.Integer(), sa.ForeignKey("crews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("crew_id", "user_id", name="uq_crew_members"),
    )
    op.create_index("ix_crew_members_crew_id", "crew_members", ["crew_id"])
    op.create_index("ix_crew_members_user_id", "crew_members", ["user_id"])


def downgrade() -> None:
    op.drop_table("crew_members")
    op.drop_table("crews")
