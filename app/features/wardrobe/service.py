"""The wardrobe's contract: coins (a ledger and a balance), owning, buying and wearing items. Other features call:

    from app.features.wardrobe import service as wardrobe

    wardrobe.pay(db, user_id, 50, "my_feature", ref)   # once per (player, reason, ref); returns what was paid
    wardrobe.grant(db, user_id, "silver_crown")         # once per player; returns True when it's new
    wardrobe.style_overrides(db, user_id)               # what the player wears, for games: {hat, accessory, items}

Coins are earned only, never bought with money. Every change is a ledger row with its reason, and the balance is kept
next to it so spending can't go below zero even when two requests race.
"""
import logging
import random
import time
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events
from ...models import Score
from ..progress import service as progress
from .catalog import BY_KEY, CATALOG, STYLE_SLOTS, Item, on_sale
from .models import CoinEntry, OwnedItem, Wallet, WornItem

log = logging.getLogger(__name__)

DAILY_CAP = 300  # coins a day from playing (runs, goals); big moments (tournaments, #1, streaks) aren't capped
FEATURED = 4  # the shop's daily picks
FEATURED_DISCOUNT = 0.2
SHARE_TTL = 600  # seconds the "owned by n% of players" figures are kept


def today() -> date:
    return progress.local_day(progress.now())


# ---- coins

def _locked_wallet(db: Session, user_id: int) -> Wallet:
    """The player's wallet, made if needed and locked until the commit, so their coin changes queue up one at a time."""
    for _ in (1, 2):
        wallet = db.scalar(select(Wallet).where(Wallet.user_id == user_id).with_for_update().execution_options(populate_existing=True))
        if wallet is not None:
            return wallet
        db.add(Wallet(user_id=user_id, balance=0))
        try:
            db.flush()
        except IntegrityError:  # made by another request a moment ago: lock that one
            db.rollback()
    raise RuntimeError("couldn't make a wallet")


def balance(db: Session, user_id: int) -> int:
    return db.scalar(select(Wallet.balance).where(Wallet.user_id == user_id)) or 0


def earned_today(db: Session, user_id: int, day: date | None = None) -> int:
    """Coins from playing today, which count towards DAILY_CAP."""
    return db.scalar(select(func.coalesce(func.sum(CoinEntry.amount), 0)).where(
        CoinEntry.user_id == user_id, CoinEntry.day == (day or today()), CoinEntry.capped.is_(True))) or 0


def pay(db: Session, user_id: int, amount: int, reason: str, ref: str, capped: bool = False) -> int:
    """Give coins once per (player, reason, ref). A capped payment is cut to what's left of today's DAILY_CAP.
    Returns the coins actually paid: 0 when it was paid before, the cap is reached, or the amount isn't positive."""
    if amount <= 0:
        return 0
    day = today()
    wallet = _locked_wallet(db, user_id)
    if capped:
        amount = min(amount, DAILY_CAP - earned_today(db, user_id, day))
        if amount <= 0:
            db.rollback()
            return 0
    db.add(CoinEntry(user_id=user_id, amount=amount, reason=reason, ref=ref[:120], capped=capped, day=day))
    wallet.balance += amount
    try:
        db.commit()
    except IntegrityError:  # paid for this already
        db.rollback()
        return 0
    events.publish("coins_earned", {"user_id": user_id, "amount": amount, "reason": reason})
    return amount


def adjust(db: Session, user_id: int, amount: int, ref: str) -> int:
    """An admin adds coins, or takes them back (negative; never below zero). Returns the new balance."""
    wallet = _locked_wallet(db, user_id)
    amount = max(amount, -wallet.balance)
    db.add(CoinEntry(user_id=user_id, amount=amount, reason="admin", ref=f"{ref}:{time.time_ns()}", day=today()))
    wallet.balance += amount
    db.commit()
    return wallet.balance


def ledger(db: Session, user_id: int, limit: int = 50) -> list[CoinEntry]:
    return list(db.scalars(select(CoinEntry).where(CoinEntry.user_id == user_id).order_by(CoinEntry.id.desc()).limit(limit)))


# ---- items

def owned(db: Session, user_id: int) -> dict[str, str]:
    """Item key -> how the player got it. Items the Progress Engine awarded (rules.py) count as earned."""
    out = {row.item_key: row.source for row in db.scalars(select(OwnedItem).where(OwnedItem.user_id == user_id))}
    for award in progress.awards_of(db, user_id):
        if award.kind == "item" and award.key in BY_KEY:
            out.setdefault(award.key, "earned")
    return out


def grant(db: Session, user_id: int, key: str, source: str = "earned") -> bool:
    """Give a player an item. Returns True when it's new to them."""
    if key not in BY_KEY or key in owned(db, user_id):
        return False
    db.add(OwnedItem(user_id=user_id, item_key=key, source=source))
    try:
        db.commit()
    except IntegrityError:  # granted by another request at the same moment
        db.rollback()
        return False
    events.publish("wardrobe_item_unlocked", {"user_id": user_id, "item": key})
    return True


def shop_today(day: date | None = None) -> list[tuple[Item, int]]:
    """Everything on sale today with its price; the day's FEATURED picks come first, cheaper."""
    day = day or today()
    stock = [i for i in CATALOG if on_sale(i, day)]
    picks = random.Random(day.toordinal()).sample(stock, min(FEATURED, len(stock)))
    discounted = [(i, int(round(i.price * (1 - FEATURED_DISCOUNT), -1))) for i in picks]
    return discounted + [(i, i.price) for i in stock if i not in picks]


class ShopError(Exception):
    pass


def buy(db: Session, user_id: int, key: str) -> int:
    """Buy an item from today's shop: the coins and the item change together or not at all. Returns the new balance."""
    price = dict((i.key, p) for i, p in shop_today()).get(key)
    if price is None:
        raise ShopError("That item isn't in the shop today")
    if key in owned(db, user_id):
        raise ShopError("You already have it")
    wallet = _locked_wallet(db, user_id)
    if wallet.balance < price:
        short = price - wallet.balance
        db.rollback()
        raise ShopError(f"You need {short} more coins")
    db.add(CoinEntry(user_id=user_id, amount=-price, reason="shop", ref=key, day=today()))
    db.add(OwnedItem(user_id=user_id, item_key=key, source="shop"))
    wallet.balance -= price
    try:
        db.commit()
    except IntegrityError:  # bought at the same moment in another tab
        db.rollback()
        raise ShopError("You already have it")
    return wallet.balance


def worn(db: Session, user_id: int) -> dict[str, str]:
    """Slot -> the item worn there. Items the player no longer owns are left out."""
    have = owned(db, user_id)
    return {w.slot: w.item_key for w in db.scalars(select(WornItem).where(WornItem.user_id == user_id)) if w.item_key in have}


def wear(db: Session, user_id: int, key: str) -> None:
    item = BY_KEY.get(key)
    if item is None or key not in owned(db, user_id):
        raise ShopError("You don't have that item")
    row = db.get(WornItem, (user_id, item.slot))
    if row is None:
        db.add(WornItem(user_id=user_id, slot=item.slot, item_key=key))
    else:
        row.item_key = key
    try:
        db.commit()
    except IntegrityError:  # two wears at once in the same slot: the other one wins
        db.rollback()


def take_off(db: Session, user_id: int, slot: str) -> None:
    row = db.get(WornItem, (user_id, slot))
    if row is not None:
        db.delete(row)
        db.commit()


def style_overrides(db: Session, user_id: int) -> dict:
    """What the player wears, in the shape games already read: the `hat` and `accessory` values to use instead of the
    character's own, and every worn item in `items`. Games that don't know a value fall back to their default."""
    on = worn(db, user_id)
    out: dict = {slot: on[slot] for slot in STYLE_SLOTS if slot in on}
    out["items"] = [{"key": key, "slot": slot, "rarity": BY_KEY[key].rarity} for slot, key in sorted(on.items())]
    return out


_share: tuple[float, dict[str, float]] | None = None  # (when, figures); None until worked out


def owned_share(db: Session) -> dict[str, float]:
    """Item key -> the share of players (anyone with a score) who own it, 0 to 1. Kept for SHARE_TTL seconds."""
    global _share
    if _share is not None and time.monotonic() - _share[0] < SHARE_TTL:
        return _share[1]
    players = db.scalar(select(func.count(func.distinct(Score.user_id)))) or 0
    counts = dict(db.execute(select(OwnedItem.item_key, func.count()).group_by(OwnedItem.item_key)).all())
    shares = {key: (counts.get(key, 0) / players if players else 0.0) for key in BY_KEY}
    _share = (time.monotonic(), shares)
    return shares


def reset() -> None:
    """Forget cached figures (tests)."""
    global _share
    _share = None
