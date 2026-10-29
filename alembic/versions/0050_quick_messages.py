"""follows: quick messages between friends and opponents, user mutes

Revision ID: 0050
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quick_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sender_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preset", sa.String(64), nullable=False),
        sa.Column("game_name", sa.String(40), nullable=True),
        sa.Column("challenge_code", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_quick_messages_sender_id", "quick_messages", ["sender_id"])
    op.create_index("ix_quick_messages_recipient_id", "quick_messages", ["recipient_id"])
    op.create_index("ix_quick_messages_created_at", "quick_messages", ["created_at"])

    op.create_table(
        "user_mutes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("muted_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "muted_user_id", name="uq_user_mute"),
        sa.CheckConstraint("user_id <> muted_user_id", name="ck_user_mute_not_self"),
    )
    op.create_index("ix_user_mutes_user_id", "user_mutes", ["user_id"])
    op.create_index("ix_user_mutes_muted_user_id", "user_mutes", ["muted_user_id"])


def downgrade() -> None:
    op.drop_table("user_mutes")
    op.drop_table("quick_messages")
