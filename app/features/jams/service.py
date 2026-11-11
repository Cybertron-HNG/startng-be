"""What other parts of start.ng may call (the jams contract): a jam's phase, its entries with their votes, and the
startng.json "jam" section that enters a game."""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ... import manifest
from ...models import Game, Score
from ..progress import service as progress
from .models import Jam, JamEntry, JamVote

log = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
WINNERS = 3  # the entries with the most votes when voting ends


def _aware(dt: datetime) -> datetime:
    # SQLite gives back naive datetimes; they were saved in UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def phase(jam: Jam, now: datetime | None = None) -> str:
    """"soon" (not open yet), "open" (taking entries), "voting" or "over"."""
    now = now or datetime.now(timezone.utc)
    if now < _aware(jam.opens_at):
        return "soon"
    if now < _aware(jam.entries_close_at):
        return "open"
    if now < _aware(jam.voting_ends_at):
        return "voting"
    return "over"


def by_slug(db: Session, slug: str) -> Jam | None:
    return db.scalar(select(Jam).where(Jam.slug == slug))


def all_jams(db: Session) -> list[Jam]:
    """Newest first."""
    return list(db.scalars(select(Jam).order_by(Jam.opens_at.desc(), Jam.id.desc())))


def has_played(db: Session, user_id: int, game_name: str) -> bool:
    """They opened the game from start.ng, or saved a score in it (an open isn't recorded when someone reaches a game by its own
    address, but a score is always theirs). Close to the rule for rating a game (features/catalog)."""
    if progress.game_open(db, user_id, game_name) is not None:
        return True
    return bool(db.scalar(select(func.count()).select_from(Score).where(Score.user_id == user_id, Score.game_name == game_name)))


@dataclass
class Entry:
    game: Game
    votes: int
    winner: int | None  # 1, 2, 3 for the winners once voting is over


def entries(db: Session, jam: Jam, now: datetime | None = None) -> list[Entry]:
    """The jam's entries that aren't hidden. While the jam is open or voting they are in the order they were entered, so the
    count doesn't steer anyone; once it's over, by votes (ties: entered first), with the winners marked."""
    rows = db.execute(
        select(JamEntry, Game).join(Game, Game.name == JamEntry.game_name).options(selectinload(Game.owner))
        .where(JamEntry.jam_id == jam.id, Game.hidden.is_(None)).order_by(JamEntry.created_at, JamEntry.id)
    ).all()
    counts = dict(db.execute(select(JamVote.game_name, func.count()).where(JamVote.jam_id == jam.id).group_by(JamVote.game_name)).all())
    out = [Entry(game, counts.get(game.name, 0), None) for _, game in rows]
    if phase(jam, now) == "over":
        out.sort(key=lambda e: -e.votes)  # stable: ties keep the order they were entered in
        place = 0
        for e in out:
            if e.votes == 0 or place == WINNERS:
                break
            place += 1
            e.winner = place
    return out


def is_entry(db: Session, jam: Jam, game_name: str) -> bool:
    return db.scalar(select(func.count()).select_from(JamEntry).where(JamEntry.jam_id == jam.id, JamEntry.game_name == game_name)) > 0


def votes_of(db: Session, jam: Jam, user_id: int) -> set[str]:
    return set(db.scalars(select(JamVote.game_name).where(JamVote.jam_id == jam.id, JamVote.user_id == user_id)))


def why_not_vote(db: Session, jam: Jam, user_id: int, game: Game) -> str | None:
    """Why this player can't vote for this entry now, or None if they can."""
    if phase(jam) != "voting":
        return "Voting is only open during the jam's voting week"
    if game.hidden is not None or not is_entry(db, jam, game.name):
        return "That game isn't in this jam"
    if game.owner_id == user_id:
        return "You can't vote for your own game"
    if not has_played(db, user_id, game.name):
        return "Play it first: only players who have played an entry can vote for it"
    return None


# ---- startng.json "jam": "<slug>"

def _parse_jam(value) -> str:
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise manifest.SchemaError('jam must be the jam\'s short name, like "jam": "lagos-rush-hour" (see start.ng/docs)')
    return value


def _enter(db: Session, game, slug: str) -> None:
    """Enter the game while that jam is taking entries. A jam that doesn't exist, isn't open yet or has closed is skipped (the
    game still publishes; the jam page shows which games are in)."""
    jam = by_slug(db, slug)
    if jam is None or phase(jam) != "open":
        log.info("jam %s not taking entries; %s not entered", slug, game.name)
        return
    if not is_entry(db, jam, game.name):
        db.add(JamEntry(jam_id=jam.id, game_name=game.name))
        db.flush()  # so is_entry sees it if the same publish asks again (sessions don't flush on their own)


manifest.register_section("jam", _parse_jam, _enter)
