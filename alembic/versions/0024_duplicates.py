"""duplicates (H2): game_fingerprints and duplicate_decisions, for the duplicate finder

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_fingerprints",
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), primary_key=True),
        sa.Column("index_hash", sa.String(64), nullable=True),
        sa.Column("cover_hash", sa.String(64), nullable=True),
        sa.Column("files_hash", sa.String(64), nullable=True),
        sa.Column("name_key", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("index_hash", "cover_hash", "files_hash", "name_key"):
        op.create_index(f"ix_game_fingerprints_{col}", "game_fingerprints", [col])
    op.create_table(
        "duplicate_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_a", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("game_b", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("target", sa.String(40), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("game_a", "game_b", name="uq_duplicate_decisions"),
    )
    op.create_index("ix_duplicate_decisions_game_a", "duplicate_decisions", ["game_a"])
    op.create_index("ix_duplicate_decisions_game_b", "duplicate_decisions", ["game_b"])


def downgrade() -> None:
    op.drop_table("duplicate_decisions")
    op.drop_table("game_fingerprints")
