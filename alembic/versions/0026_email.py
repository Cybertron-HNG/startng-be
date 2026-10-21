"""email (H1): email_subscribers, email_subscriber_topics and email_outbox

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_subscribers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, unique=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_token", sa.String(64), nullable=True, unique=True),
        sa.Column("confirm_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_topics", sa.JSON(), nullable=True),
        sa.Column("unsubscribe_token", sa.String(64), nullable=False, unique=True),
        sa.Column("bad_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bad_reason", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_subscribers_email", "email_subscribers", ["email"], unique=True)
    op.create_table(
        "email_subscriber_topics",
        sa.Column("subscriber_id", sa.Integer(), sa.ForeignKey("email_subscribers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("topic", sa.String(20), primary_key=True),
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subscriber_id", sa.Integer(), sa.ForeignKey("email_subscribers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_email", sa.String(255), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(200), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("send_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    for col in ("subscriber_id", "day", "status", "send_after", "provider_id"):
        op.create_index(f"ix_email_outbox_{col}", "email_outbox", [col])


def downgrade() -> None:
    op.drop_table("email_outbox")
    op.drop_table("email_subscriber_topics")
    op.drop_table("email_subscribers")
