"""Spotting copies (H2 duplicate finder).

A game's fingerprint (fingerprint()) is saved when it's published or updated. pairs() compares every live game's
fingerprint and returns the pairs that look like the same game:
    same_page   their index.html is the same (ignoring spacing)
    same_cover  their cover picture is the same file
    same_files  every file has the same path and size
    similar_name  their names are the same once versions and copies are taken off (lane-dash, lane-dash-2,
                  lanedash-copy), or one letter apart
Pairs an admin kept are left out.
"""
import hashlib
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import events, flags
from ...covers import COVER_FILE
from ...db import SessionLocal
from ...game_store import GameStore, get_game_store
from ...models import Game
from .models import Decision, Fingerprint

FLAG = "duplicates"
NEVER = {"hangout"}  # start.ng's own space
log = logging.getLogger(__name__)


def name_key(name: str) -> str:
    """The name without versions, copies and separators: lane-dash-v2, lanedash-copy and lane-dash-2 -> lanedash."""
    n = name.lower()
    while True:  # one suffix at a time: a nested repeat in one pattern can backtrack badly on long names
        stripped = re.sub(r"-?(?:copy|clone|new|final|v\d+|\d+)$", "", n)
        if stripped == n:
            break
        n = stripped
    return re.sub(r"[^a-z0-9]", "", n) or name


def one_apart(a: str, b: str) -> bool:
    """One letter added, removed or changed (for names of 6 letters or more, so short names don't all match)."""
    if a == b or min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(db: Session, store: GameStore | None, game: Game) -> Fingerprint:
    """Read the live game from storage and save its fingerprint (the caller commits)."""
    index = cover = sizes = None
    if store is not None:
        sizes = store.file_sizes(game.name)
        page = store.read_file(game.name, "index.html")
        index = _sha(re.sub(rb"\s+", b"", page)) if page else None
        if game.cover:
            pic = store.read_file(game.name, game.cover)
            cover = _sha(pic) if pic else None
    files = None
    if sizes:
        listing = sorted((p, s) for p, s in sizes.items() if p != COVER_FILE)
        files = _sha(repr(listing).encode()) if listing else None
    fp = db.get(Fingerprint, game.name) or Fingerprint(game_name=game.name)
    fp.index_hash, fp.cover_hash, fp.files_hash, fp.name_key = index, cover, files, name_key(game.name)
    db.add(fp)
    return fp


@dataclass
class Pair:
    a: Game
    b: Game
    reasons: list[str] = field(default_factory=list)


def pairs(db: Session) -> list[Pair]:
    """Look-alike pairs among live and hidden games (not ones an admin kept), the strongest first."""
    fps = {f.game_name: f for f in db.scalars(select(Fingerprint)).all() if f.game_name not in NEVER}
    games = {g.name: g for g in db.scalars(select(Game).where(Game.name.in_(list(fps)))).all()} if fps else {}
    kept = {(d.game_a, d.game_b) for d in db.scalars(select(Decision).where(Decision.decision == "keep")).all()}
    found: dict[tuple[str, str], Pair] = {}

    def add(x: str, y: str, reason: str) -> None:
        a, b = sorted((x, y))
        if (a, b) in kept or a not in games or b not in games:
            return
        p = found.setdefault((a, b), Pair(games[a], games[b]))
        if reason not in p.reasons:
            p.reasons.append(reason)

    names = sorted(fps)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            fx, fy = fps[x], fps[y]
            if fx.index_hash and fx.index_hash == fy.index_hash:
                add(x, y, "same_page")
            if fx.cover_hash and fx.cover_hash == fy.cover_hash:
                add(x, y, "same_cover")
            if fx.files_hash and fx.files_hash == fy.files_hash:
                add(x, y, "same_files")
            if fx.name_key == fy.name_key or one_apart(fx.name_key, fy.name_key):
                add(x, y, "similar_name")
    strength = {"same_files": 4, "same_page": 3, "same_cover": 2, "similar_name": 1}
    return sorted(found.values(), key=lambda p: -sum(strength[r] for r in p.reasons))


# ---- keep fingerprints current as games are published and updated (only while the feature is on)


def _on_publish(payload: dict) -> None:
    if not flags.is_on(FLAG) or payload["game"] in NEVER:
        return
    with SessionLocal() as db:
        game = db.get(Game, payload["game"])
        if game is None:
            return
        try:
            fingerprint(db, get_game_store(), game)
            db.commit()
        except Exception:
            log.exception("Couldn't fingerprint %s", payload["game"])


events.subscribe("game_published")(_on_publish)
events.subscribe("game_updated")(_on_publish)
