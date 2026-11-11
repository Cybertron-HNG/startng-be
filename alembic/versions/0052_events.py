"""events: events and event_images

Revision ID: 0052
Revises: 0049
(Open PRs take 0050 (live) and 0051 (jams). Whichever merges after them: set down_revision to the newest one first.)
"""
from alembic import op
import sqlalchemy as sa

revision = "0052"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("tagline", sa.String(160)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("venue", sa.String(120), nullable=False),
        sa.Column("address", sa.String(200)),
        sa.Column("ticket_url", sa.String(300)),
        sa.Column("ticket_label", sa.String(30), nullable=False, server_default="Get tickets"),
        sa.Column("website_url", sa.String(300)),
        sa.Column("highlights", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("partners", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("spotlight", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # At most one spotlighted event: a unique index over the rows where spotlight is true.
    op.create_index("uq_events_one_spotlight", "events", ["spotlight"], unique=True, postgresql_where=sa.text("spotlight"), sqlite_where=sa.text("spotlight"))
    op.create_table(
        "event_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alt", sa.String(160)),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_images_event_id", "event_images", ["event_id"])


def downgrade() -> None:
    op.drop_table("event_images")
    op.drop_table("events")
