"""follows: who follows whom, and friend invite links (app/features/follows)

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "follows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("follower_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("follower_id", "target_user_id", name="uq_follows_pair"),
        sa.CheckConstraint("follower_id <> target_user_id", name="ck_follows_not_self"),
    )
    op.create_index("ix_follows_follower_id", "follows", ["follower_id"])
    op.create_index("ix_follows_target_user_id", "follows", ["target_user_id"])
    op.create_table(
        "friend_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("inviter_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_friend_invites_inviter_id", "friend_invites", ["inviter_id"])
    op.create_index("ix_friend_invites_expires_at", "friend_invites", ["expires_at"])


def downgrade() -> None:
    op.drop_table("friend_invites")
    op.drop_table("follows")
