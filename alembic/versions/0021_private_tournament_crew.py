"""private tournaments can be made for a crew (private_tournaments.crew_id)

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
    op.add_column("private_tournaments", sa.Column("crew_id", sa.Integer(), nullable=True))
    op.create_index("ix_private_tournaments_crew_id", "private_tournaments", ["crew_id"])


def downgrade() -> None:
    op.drop_index("ix_private_tournaments_crew_id", table_name="private_tournaments")
    op.drop_column("private_tournaments", "crew_id")
