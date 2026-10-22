"""wardrobe: wallets, the coin ledger, owned and worn items, and game goals (app/features/wardrobe)

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
    op.create_table(
        "wallets",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("balance >= 0", name="ck_wallets_balance"),
    )
    op.create_table(
        "coin_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "reason", "ref", name="uq_coin_ledger_user_reason_ref"),
    )
    op.create_index("ix_coin_ledger_user_day", "coin_ledger", ["user_id", "day"])
    op.create_table(
        "owned_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "item_key", name="uq_owned_items_user_item"),
    )
    op.create_index("ix_owned_items_user_id", "owned_items", ["user_id"])
    op.create_table(
        "worn_items",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("slot", sa.String(16), primary_key=True),
        sa.Column("item_key", sa.String(64), nullable=False),
    )
    op.create_table(
        "game_goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_name", sa.String(40), sa.ForeignKey("games.name", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("stat", sa.String(32), nullable=False),
        sa.Column("at_least", sa.Boolean(), nullable=False),
        sa.Column("target", sa.BigInteger(), nullable=False),
        sa.Column("daily", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("game_name", "key", name="uq_game_goals_game_key"),
    )
    op.create_index("ix_game_goals_game_name", "game_goals", ["game_name"])


def downgrade() -> None:
    op.drop_index("ix_game_goals_game_name", table_name="game_goals")
    op.drop_table("game_goals")
    op.drop_table("worn_items")
    op.drop_index("ix_owned_items_user_id", table_name="owned_items")
    op.drop_table("owned_items")
    op.drop_index("ix_coin_ledger_user_day", table_name="coin_ledger")
    op.drop_table("coin_ledger")
    op.drop_table("wallets")
