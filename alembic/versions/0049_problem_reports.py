"""support (F10): problem_reports, players' reports of problems with start.ng

Revision ID: 0049
Revises: 0048
"""
from alembic import op
import sqlalchemy as sa

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "problem_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("what", sa.Text(), nullable=False),
        sa.Column("page", sa.String(300), nullable=True),
        sa.Column("browser", sa.String(120), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("similar", sa.JSON(), nullable=True),
        sa.Column("duplicate_of", sa.Integer(), sa.ForeignKey("problem_reports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_problem_reports_user_id", "problem_reports", ["user_id"])
    op.create_index("ix_problem_reports_status", "problem_reports", ["status"])
    op.create_index("ix_problem_reports_created_at", "problem_reports", ["created_at"])


def downgrade() -> None:
    op.drop_table("problem_reports")
