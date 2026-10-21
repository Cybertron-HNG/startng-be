"""progress engine, second drop: the tournament a run counted for, and game opens (app/features/progress)

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("tournament_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_runs_tournament_id", "tournaments", ["tournament_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_runs_created", "runs", ["created_at"])
    op.create_table(
        "game_opens",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_table("game_opens")
    op.drop_index("ix_runs_created", table_name="runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_tournament_id", type_="foreignkey")
        batch.drop_column("tournament_id")
