"""kit: games.uses_kit, whether a game's live version loads https://start.ng/kit.js (PRD M1)

Revision ID: 0040
Revises: 0039
"""
from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("uses_kit", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("games", "uses_kit")
