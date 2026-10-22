"""The challenge's share card (B2, kind "challenge"): "Loki scored 4,200 on Relic Hunter. Can you beat it?"."""
from sqlalchemy.orm import Session

from ...routers.games import _player_name
from ...routers.pages import _title
from ..share_cards import service as share
from . import service


def describe(db: Session, code: str) -> share.Card | None:
    c = service.find(db, code)
    if c is None:
        return None
    name, game, score = _player_name(c.challenger), _title(c.game_name), service.format_score(c.game, c.score)
    takers = len(service.attempts(db, c))
    facts = [(str(takers), "took it on")] if takers else []
    return share.Card(
        eyebrow="Challenge", name=name, title=f"scored {score}", facts=facts, game=game,
        badge=share.Badge("?", label="your turn"), cta=f"Can you beat {score}?", action="Take the challenge",
        path=f"/c/{c.code}", text=f"I scored {score} on {game} on start.ng. Can you beat it?",
    )


share.register("challenge", describe)
