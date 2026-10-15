"""game-defined leaderboards

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("leaderboard", sa.JSON()))
    op.add_column("scores", sa.Column("stats", sa.JSON()))


def downgrade() -> None:
    op.drop_column("scores", "stats")
    op.drop_column("games", "leaderboard")
