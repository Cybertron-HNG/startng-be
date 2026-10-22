"""catalog (T1): game_catalog (category and rating totals) and game_ratings

Revision ID: 0035
Revises: 0034
"""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_catalog",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("category", sa.String(24)),
        sa.Column("category_source", sa.String(8)),
        sa.Column("rating_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rating_avg", sa.Float),
    )
    op.create_index("ix_game_catalog_category", "game_catalog", ["category"])
    op.create_table(
        "game_ratings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("stars", sa.SmallInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "game_name", name="uq_game_ratings_user_game"),
    )
    op.create_index("ix_game_ratings_user_id", "game_ratings", ["user_id"])
    op.create_index("ix_game_ratings_game_name", "game_ratings", ["game_name"])


def downgrade() -> None:
    op.drop_table("game_ratings")
    op.drop_table("game_catalog")
