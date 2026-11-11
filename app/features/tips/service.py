"""What other features may call: sending a tip, and the counts and lists the pages show."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..catalog import service as catalog
from ..moderation import service as moderation
from ..wardrobe import service as wardrobe
from .models import Tip

AMOUNTS = (10, 25, 50)  # the tip buttons; twin in startng-fe src/features/tips/TipBox.tsx
DAILY_TIPS = 3  # tips a player can send in a day
DAILY_COINS = 100  # coins a player can send in a day
NOTE_MAX = 140
# Coins that can't be passed on: gifts from admins, and tips the player was given (so coins can't be moved in circles).
KEPT_REASONS = ("admin", "tip_in")


class TipError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def sent_today(db: Session, user_id: int) -> tuple[int, int]:
    """How many tips the player sent today, and how many coins."""
    count, coins = db.execute(select(func.count(), func.coalesce(func.sum(Tip.amount), 0)).where(
        Tip.from_user_id == user_id, Tip.day == wardrobe.today())).one()
    return int(count), int(coins)


def why_not(db: Session, user_id: int, game) -> str | None:
    """Why this player can't tip this game's creator right now (shown to them), or None when they can."""
    if game.owner_id == user_id:
        return "You can't tip yourself"
    if not catalog.has_played(db, user_id, game.name):
        return "Play the game first, then you can tip its creator"
    count, coins = sent_today(db, user_id)
    if count >= DAILY_TIPS:
        return f"You've sent {DAILY_TIPS} tips today. Come back tomorrow"
    if coins >= DAILY_COINS:
        return f"You've sent {DAILY_COINS} coins in tips today. Come back tomorrow"
    return None


def spendable(db: Session, user_id: int) -> int:
    """The coins a player can tip: their balance without coins from admins or from tips they were given."""
    return max(0, wardrobe.balance(db, user_id) - max(0, wardrobe.total_for(db, user_id, KEPT_REASONS)))


def send(db: Session, user_id: int, game, amount: int, note: str | None) -> int:
    """Send a tip. Raises TipError with a message for the player. Returns their new coin balance."""
    if amount not in AMOUNTS:
        raise TipError("bad_amount", "Pick 10, 25 or 50 coins", 422)
    reason = why_not(db, user_id, game)
    if reason:
        raise TipError("not_allowed", reason)
    _, coins = sent_today(db, user_id)
    if coins + amount > DAILY_COINS:
        raise TipError("daily_coins", f"You can send {DAILY_COINS - coins} more coins in tips today")
    note = " ".join((note or "").split())[:NOTE_MAX] or None
    if note and moderation.check_text(note, db):
        raise TipError("text_not_allowed", "Your note has a word that isn't allowed on start.ng. Please change it.", 422)
    tip = Tip(from_user_id=user_id, to_user_id=game.owner_id, game_name=game.name, amount=amount, note=note, day=wardrobe.today())
    db.add(tip)
    db.flush()
    try:
        return wardrobe.transfer(db, user_id, game.owner_id, amount, f"tip:{tip.id}", "tip_out", "tip_in", keep=KEPT_REASONS)
    except wardrobe.ShopError as err:  # not enough coins: the transfer rolled the tip back too
        raise TipError("not_enough_coins", str(err)) from err


def supporters(db: Session, creator_id: int) -> int:
    """How many different players have tipped this creator."""
    return db.scalar(select(func.count(func.distinct(Tip.from_user_id))).where(Tip.to_user_id == creator_id)) or 0


def received(db: Session, game_name: str, limit: int = 20) -> list[Tip]:
    """The newest tips for a game, for its creator."""
    return list(db.scalars(select(Tip).where(Tip.game_name == game_name).order_by(Tip.id.desc()).limit(limit)))


def received_total(db: Session, game_name: str) -> tuple[int, int]:
    """How many tips a game got, and how many coins."""
    count, coins = db.execute(select(func.count(), func.coalesce(func.sum(Tip.amount), 0)).where(Tip.game_name == game_name)).one()
    return int(count), int(coins)
