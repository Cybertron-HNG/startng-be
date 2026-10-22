"""The wardrobe's own tables. Migration: alembic/versions/0041_wardrobe.py."""
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Wallet(Base):
    """A player's coin balance. Always the sum of their ledger; kept here so spending is one guarded update."""

    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallets_balance"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=0)


class CoinEntry(Base):
    """One change to a player's coins and why. Unique per player, reason and ref, so paying the same thing twice
    does nothing."""

    __tablename__ = "coin_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "reason", "ref", name="uq_coin_ledger_user_reason_ref"),
        Index("ix_coin_ledger_user_day", "user_id", "day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount: Mapped[int] = mapped_column(Integer)  # spending is negative
    reason: Mapped[str] = mapped_column(String(32))  # "first_run", "game_goal", "shop", "admin", ...
    ref: Mapped[str] = mapped_column(String(120))  # what it was for: a day, a game, a goal, an item
    capped: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # counts towards the daily cap
    day: Mapped[date] = mapped_column(Date)  # Nigeria time, for the daily cap
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OwnedItem(Base):
    """An item a player owns. Items earned through the Progress Engine (its `awards` table) count too; see service.owned."""

    __tablename__ = "owned_items"
    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_owned_items_user_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))  # "earned", "shop" or "admin"
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WornItem(Base):
    """What a player wears in each slot (nothing in a slot = no row)."""

    __tablename__ = "worn_items"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    slot: Mapped[str] = mapped_column(String(16), primary_key=True)
    item_key: Mapped[str] = mapped_column(String(64))


class GameGoal(Base):
    """A goal a game declares in its startng.json `rewards` section. start.ng decides what it pays (goals.coins_for)."""

    __tablename__ = "game_goals"
    __table_args__ = (UniqueConstraint("game_name", "key", name="uq_game_goals_game_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(80))
    stat: Mapped[str] = mapped_column(String(32))
    at_least: Mapped[bool] = mapped_column(Boolean)  # True: stat >= target; False: stat <= target (times)
    target: Mapped[int] = mapped_column(BigInteger)
    daily: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # can be earned again each day
