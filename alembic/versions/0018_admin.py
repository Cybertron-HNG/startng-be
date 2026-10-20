"""admin: roles, admins, game reports, developer contacts, the activity log; users.blocked_at, games.hidden

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("games", sa.Column("hidden", sa.String(20), nullable=True))
    op.add_column("games", sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_games_hidden", "games", ["hidden"])
    op.create_table(
        "admin_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("admin_roles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("added_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admins_email", "admins", ["email"], unique=True)
    op.create_table(
        "game_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("reporter_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("by_admin", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_game_reports_game_status", "game_reports", ["game_name", "status"])
    op.create_index("ix_game_reports_reporter_id", "game_reports", ["reporter_id"])
    op.create_index("ix_game_reports_status", "game_reports", ["status"])
    op.create_index("ix_game_reports_created_at", "game_reports", ["created_at"])
    op.create_table(
        "developer_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("developer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), nullable=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("game_reports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("admin_email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_developer_contacts_developer_id", "developer_contacts", ["developer_id"])
    op.create_table(
        "activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target", sa.String(255), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_activity_at", "activity", ["at"])
    op.create_index("ix_activity_kind", "activity", ["kind"])


def downgrade() -> None:
    op.drop_table("activity")
    op.drop_table("developer_contacts")
    op.drop_table("game_reports")
    op.drop_table("admins")
    op.drop_table("admin_roles")
    op.drop_index("ix_games_hidden", table_name="games")
    op.drop_column("games", "hidden_at")
    op.drop_column("games", "hidden")
    op.drop_column("users", "blocked_at")
