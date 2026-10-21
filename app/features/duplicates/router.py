"""The duplicate finder's API (H2). Admins: /admin/duplicates. Owners: /duplicates/mine. 404 while the flag is off."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...game_store import GameStore, get_game_store
from ...models import Game, User
from ...routers.games import _cover_url, _game_or_404, _player_name
from ...security import current_user
from ...storage import game_url
from . import service
from .models import Decision

REASON = "duplicate"  # games.hidden for a game hidden as a copy
adm.register_hide_reason(REASON)

router = APIRouter(tags=["duplicates"])
manage = require("games.duplicates")


class GameSide(BaseModel):
    name: str
    url: str
    cover_url: str | None
    owner_id: int
    owner: str
    created_at: str
    hidden: str | None


class PairOut(BaseModel):
    a: GameSide  # the older game (published first)
    b: GameSide
    reasons: list[str]
    same_owner: bool


class KeepIn(BaseModel):
    a: str
    b: str


class HideIn(BaseModel):
    of: str  # the game it's a copy of


class HiddenOut(BaseModel):
    game: GameSide
    of: str | None
    decided_by: str
    decided_at: str


class MineOut(BaseModel):
    game: str
    of: str | None


def _side(g: Game) -> GameSide:
    return GameSide(name=g.name, url=game_url(g.name), cover_url=_cover_url(g), owner_id=g.owner_id,
                    owner=_player_name(g.owner, g.owner.email), created_at=g.created_at.isoformat(), hidden=g.hidden)


def _pair_key(x: str, y: str) -> tuple[str, str]:
    return tuple(sorted((x, y)))  # type: ignore[return-value]


@router.get("/admin/duplicates", response_model=list[PairOut])
def possible_duplicates(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Pairs of games that look like the same game, the strongest match first. The older game comes first."""
    out = []
    for p in service.pairs(db):
        older, newer = sorted((p.a, p.b), key=lambda g: (g.created_at, g.name))
        if older.hidden == REASON and newer.hidden == REASON:
            continue
        out.append(PairOut(a=_side(older), b=_side(newer), reasons=p.reasons, same_owner=older.owner_id == newer.owner_id))
    return out


@router.post("/admin/duplicates/scan")
def scan(admin: AdminUser = Depends(manage), db: Session = Depends(get_db), store: GameStore | None = Depends(get_game_store)):
    """Fingerprint every game (for games published before the finder was on). Reads each game from storage."""
    games = db.scalars(select(Game).where(Game.name.not_in(service.NEVER))).all()
    for g in games:
        service.fingerprint(db, store, g)
    adm.log(db, admin, "duplicates.scan", None, games=len(games))
    db.commit()
    return {"scanned": len(games)}


@router.post("/admin/duplicates/keep", status_code=status.HTTP_204_NO_CONTENT)
def keep(body: KeepIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Both games are fine: stop showing this pair."""
    _game_or_404(db, body.a)
    _game_or_404(db, body.b)
    a, b = _pair_key(body.a, body.b)
    if not db.scalar(select(Decision.id).where(Decision.game_a == a, Decision.game_b == b, Decision.decision == "keep")):
        db.add(Decision(game_a=a, game_b=b, decision="keep", decided_by=admin.email))
        adm.log(db, admin, "duplicates.keep", f"{a} + {b}")
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/duplicates/games/{name}/hide", status_code=status.HTTP_204_NO_CONTENT)
def hide(name: str, body: HideIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db),
         store: GameStore | None = Depends(get_game_store)):
    """Hide a game as a copy of another. Its owner sees which one in Your games."""
    game = _game_or_404(db, name)
    _game_or_404(db, body.of)
    if name == body.of:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A game can't be a copy of itself")
    a, b = _pair_key(name, body.of)
    row = db.scalar(select(Decision).where(Decision.game_a == a, Decision.game_b == b))
    if row is None:
        row = Decision(game_a=a, game_b=b)
        db.add(row)
    row.decision, row.target, row.decided_by, row.decided_at = "hidden", name, admin.email, adm.now()
    adm.log(db, admin, "duplicates.hide", name, of=body.of)
    adm.hide_game(db, store, game, REASON)  # commits
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admin/duplicates/hidden", response_model=list[HiddenOut])
def hidden(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Games hidden as copies, newest first."""
    out = []
    for g in db.scalars(select(Game).where(Game.hidden == REASON).order_by(Game.hidden_at.desc())).all():
        d = db.scalar(select(Decision).where(Decision.target == g.name, Decision.decision == "hidden").order_by(Decision.decided_at.desc()))
        of = (d.game_b if d.game_a == g.name else d.game_a) if d else None
        out.append(HiddenOut(game=_side(g), of=of, decided_by=d.decided_by if d else "", decided_at=d.decided_at.isoformat() if d else ""))
    return out


@router.post("/admin/duplicates/games/{name}/show", status_code=status.HTTP_204_NO_CONTENT)
def show(name: str, admin: AdminUser = Depends(manage), db: Session = Depends(get_db), store: GameStore | None = Depends(get_game_store)):
    """It isn't a copy after all: put it back, and stop showing the pair."""
    game = _game_or_404(db, name)
    if game.hidden != REASON:
        raise HTTPException(status.HTTP_409_CONFLICT, "It isn't hidden as a copy")
    for d in db.scalars(select(Decision).where(Decision.target == name, Decision.decision == "hidden")).all():
        d.decision, d.target, d.decided_by, d.decided_at = "keep", None, admin.email, adm.now()
    adm.log(db, admin, "duplicates.show", name)
    adm.show_game(db, store, game)  # commits
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/admin/duplicates/games/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete(name: str, admin: AdminUser = Depends(manage), db: Session = Depends(get_db),
           store: GameStore | None = Depends(get_game_store)):
    """Delete a copy for good: its files, scores and plays. Can't be undone."""
    game = _game_or_404(db, name)
    if name in service.NEVER:
        raise HTTPException(status.HTTP_409_CONFLICT, "That's start.ng's own game")
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Game storage isn't set up, so the game can't be removed")
    store.delete_game(name)
    store.set_hidden(name, False)
    adm.log(db, admin, "duplicates.delete", name, owner=game.owner.email)
    db.delete(game)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/duplicates/mine", response_model=list[MineOut])
def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner's games hidden as copies, and of which game."""
    out = []
    for g in db.scalars(select(Game).where(Game.owner_id == user.id, Game.hidden == REASON)).all():
        d = db.scalar(select(Decision).where(Decision.target == g.name, Decision.decision == "hidden"))
        out.append(MineOut(game=g.name, of=(d.game_b if d.game_a == g.name else d.game_a) if d else None))
    return out

