"""The "challenge" share card (B2's contract C8): "Loki scored 4,200 on Relic Hunter. Can you beat it?"."""
from sqlalchemy.orm import Session

from ...features.share import service as share
from ...models import Game
from ...routers.games import _cover_url, _player_name
from ...routers.pages import _title
from . import service


def describe(db: Session, code: str) -> share.Card | None:
    c = service.find(db, code)
    if c is None:
        return None
    game = db.get(Game, c.game_name)
    stat = service.schema_of(game)[0]
    shown = service.format_stat(stat, c.stats.get(stat["key"], c.score))
    name, title = _player_name(c.challenger), _title(game.name)
    cover = _cover_url(game)
    return share.Card(
        kicker="Challenge", title=f"{name} scored {shown} on {title}", subtitle="Can you beat it?",
        numbers=[(stat["label"], shown)], covers=[cover] if cover else [], target=f"/c/{c.code}",
        text=f"I scored {shown} on {title} on start.ng. Can you beat it?",
        description=f"{name} scored {shown} ({stat['label']}) on {title}. Take the challenge and see if you can beat it.")


share.register("challenge", describe)
