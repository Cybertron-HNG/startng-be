"""What other features may call (this feature's contract): snapshot()."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ... import events, flags, tournaments
from ...db import SessionLocal
from ...models import Game, User
from ...routers.games import _player_name
from ...routers.pages import _title
from ..kit import service as kit
from ..profiles import service as profiles
from .models import LiveMoment

FLAG = "live"
SHOW_FROM = 2  # "N playing now" only from this many, so a game never says "0 playing" or looks lonely with 1
MOMENTS = 6  # shown on the homepage
MOMENTS_FOR = timedelta(hours=24)  # older moments aren't "live" any more
KEEP = timedelta(days=7)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when  # SQLite drops the zone


def record_moment(db: Session, kind: str, game: str, user_id: int, at: datetime | None = None) -> None:
    db.add(LiveMoment(kind=kind, game_name=game, user_id=user_id, at=at or now()))
    db.execute(delete(LiveMoment).where(LiveMoment.at < (at or now()) - KEEP))


@events.subscribe("record_beaten")
def _on_record(payload: dict) -> None:
    if not flags.is_on(FLAG):
        return
    with SessionLocal() as db:
        record_moment(db, "record", payload["game"], payload["user_id"])
        db.commit()


def _moments(db: Session, at: datetime) -> list[dict]:
    rows = db.execute(select(LiveMoment, User, Game).join(User, User.id == LiveMoment.user_id).join(Game, Game.name == LiveMoment.game_name)
                      .where(LiveMoment.at >= at - MOMENTS_FOR, Game.hidden.is_(None)).order_by(LiveMoment.at.desc()).limit(MOMENTS)).all()
    out = []
    for m, user, game in rows:
        # Players who keep their profile private are never named (profiles' own rule for what's public).
        who = _player_name(user) if profiles.is_public(db, user.id) else "Someone"
        out.append({"kind": m.kind, "game": game.name, "title": _title(game.name), "who": who,
                    "text": f"{who} took #1 on {_title(game.name)}", "at": _utc(m.at)})
    return out


def snapshot(db: Session, at: datetime | None = None) -> dict:
    """What's happening now: {playing: {game: n (n >= SHOW_FROM)}, moments: [...], tournament_playing: n}."""
    at = at or now()
    live = {g: n for g, n in kit.playing_now(db, now=at).items() if g != "hangout"}
    shown = {g: n for g, n in live.items() if n >= SHOW_FROM}
    t = tournaments.running(db, at)
    on_tournament = sum(live.get(tg.game_name, 0) for tg in t.games) if t else 0
    return {"playing": shown, "moments": _moments(db, at),
            "tournament_playing": on_tournament if on_tournament >= SHOW_FROM else 0}
