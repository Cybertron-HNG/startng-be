"""The "creator" share card (B2's contract C8): who made the games, how many, and how many people played this month."""
from sqlalchemy.orm import Session

from ...models import User
from ...routers.games import _player_name
from ..share_cards import service as share
from . import service


def describe(db: Session, item: str) -> share.Card | None:
    if not item.isdigit():
        return None
    user = db.get(User, int(item))
    games = service.visible_games(db, int(item)) if user else []
    if user is None or not games:
        return None
    name = _player_name(user)
    players = service.unique_visitors(db, [g.name for g in games])
    return share.Card(
        eyebrow="Creator", name=name, title="makes games on start.ng",
        facts=[(str(len(games)), "game" if len(games) == 1 else "games"), (f"{players:,}", f"{'player' if players == 1 else 'players'}, 30 days")],
        cta=service.bio_of(db, user.id) or "Play their games, free in your browser", path=f"/by/{service.slug_for(user)}",
        text=f"Play {name}'s games on start.ng.")


share.register("creator", describe)
