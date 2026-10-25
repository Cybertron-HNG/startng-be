"""The door for the daily agent that writes richer pages (a scheduled Claude Code agent, once every 24 hours).

The admin routes need a person signed in with Google, and that sign-in expires, so an agent cannot use them. These routes use
one secret key instead (AGENT_API_KEY, sent as "Authorization: Bearer <key>"). With no key set the routes answer 404, as if they
did not exist. The agent can only do two things: read the games that still need pages (their facts and brief, all public repo
text) and save a page it wrote. A saved page is checked here exactly like any other: its quotes must really be in the game's
own text, it passes the safety check (service.is_safe), it is not a copy of another game's page, and it never replaces a page a
person approved, edited or took down. A page that passes goes live by itself (GAME_PAGES_AUTO_APPROVE), like the others."""
import importlib
import re
import secrets
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_db
from ...models import Game
from . import generate as gen
from . import service
from .facts import read_facts
from .models import GamePage
from .router import GAME, KIND, router

_router_module = importlib.import_module(f"{__package__}.router")  # the store is looked up through it, so tests can swap it in one place

README_SENT = 3_000  # characters of the README the agent is shown
MIN_QUOTES, MIN_QUOTE_LEN = 2, 12


def agent_only(authorization: str | None = Header(default=None)) -> None:
    key = settings.agent_api_key
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not key or not given or not secrets.compare_digest(given.encode(), key.encode()):
        raise HTTPException(404, "Not Found")  # the same answer as a route that does not exist


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _evidence(game: Game, facts_dict: dict) -> str:
    """Everything start.ng knows about the game in the game's own words: what a quote has to be found in."""
    brief = game.brief or {}
    parts = [game.description or "", brief.get("readme") or "", *(brief.get("messages") or []), *(brief.get("notes") or [])]

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(facts_dict)
    return _norm("\n".join(parts))


class Todo(BaseModel):
    game: str
    title: str
    by: str
    kinds: list[str]
    facts: dict
    readme: str | None
    messages: list[str]
    notes: list[str]
    names: list[str]


@router.get("/agent/game-pages/todo", response_model=list[Todo], dependencies=[Depends(agent_only)])
def agent_todo(limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
    """Games that have no page written by the agent yet, newest first. A game a person has touched is left out."""
    done = set(db.scalars(select(GamePage.game_name).where(GamePage.kind == "about", GamePage.writer == "agent")).all())
    touched = set(db.scalars(select(GamePage.game_name).where((GamePage.edited.is_(True)) | (GamePage.held.is_(True)))).all())
    store = _router_module.get_game_store()
    out: list[Todo] = []
    for game in db.scalars(service._visible().order_by(Game.created_at.desc())).all():
        if game.name in done or game.name in touched:
            continue
        facts = read_facts(store, game, service._tags(db, game.name))
        brief = game.brief or {}
        out.append(Todo(
            game=game.name, title=facts.title, by=facts.creator, kinds=list(gen.KINDS), facts=facts.as_dict(),
            readme=(brief.get("readme") or "")[:README_SENT] or None, messages=brief.get("messages") or [], notes=brief.get("notes") or [],
            names=brief.get("names") or [],
        ))
        if len(out) >= limit:
            break
    return out


class Save(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    quotes: list[str] = Field(default_factory=list, description="Exact phrases from the game's own text that the page rests on")


def _blocked(db: Session, game_name: str, kind: str) -> GamePage | None:
    row = db.scalar(select(GamePage).where(GamePage.game_name == game_name, GamePage.kind == kind))
    if row is not None and (row.edited or row.held or (row.status == "approved" and row.approved_by_id is not None)):
        raise HTTPException(409, "A person approved, edited or took down this page, so the agent leaves it alone")
    return row


@router.put("/agent/game-pages/{name}/{kind}", dependencies=[Depends(agent_only)])
def agent_save(body: Save, name: str = GAME, kind: str = KIND, db: Session = Depends(get_db)):
    game = db.get(Game, name)
    if game is None or game.hidden is not None:
        raise HTTPException(404, "No such game")
    row = _blocked(db, name, kind)
    store = _router_module.get_game_store()
    facts = read_facts(store, game, service._tags(db, name))
    evidence = _evidence(game, facts.as_dict())
    quotes = [q.strip() for q in body.quotes if len(q.strip()) >= MIN_QUOTE_LEN]
    if len(quotes) < MIN_QUOTES:
        raise HTTPException(422, f"Give at least {MIN_QUOTES} quotes (each {MIN_QUOTE_LEN}+ characters) copied from the game's own text")
    missing = [q for q in quotes if _norm(q) not in evidence]
    if missing:
        raise HTTPException(422, f'This quote is not in the game\'s own text: "{missing[0][:80]}". Nothing was saved.')
    title, description, text = body.title.strip(), body.description.strip(), body.body.strip()
    if not service.is_safe(title, description, text):
        raise HTTPException(422, "The text has a link, markup, an address or a word that is not allowed")
    if row is None:
        row = GamePage(game_name=name, kind=kind)
        db.add(row)
    row.title, row.description, row.body, row.reason = title, description, text, None
    row.writer, row.status, row.edited = "agent", "draft", False
    row.facts = {"written_by": "agent", "quotes": quotes}
    row.generated_at, row.approved_at, row.approved_by_id = datetime.now(timezone.utc), None, None
    db.flush()
    twin = service._similar_to(db, row, facts.title)
    if twin:
        row.status, row.reason = "skipped", f"Too much like the {gen.SHORT[kind]} page of {twin}."
    else:
        service._auto_approve(row)
    db.commit()
    return {"game": name, "kind": kind, "status": row.status, "reason": row.reason}


@router.post("/agent/game-pages/{name}/skip", dependencies=[Depends(agent_only)])
def agent_skip(reason: str = Query("Not enough to write about", max_length=200), name: str = GAME, db: Session = Depends(get_db)):
    """The agent read the game and found too little to write from: mark it so the game is not offered again."""
    game = db.get(Game, name)
    if game is None or game.hidden is not None:
        raise HTTPException(404, "No such game")
    row = _blocked(db, name, "about")
    if row is None:
        row = GamePage(game_name=name, kind="about")
        db.add(row)
    if row.status != "approved":
        row.status, row.reason, row.writer = "skipped", re.sub(r"\s+", " ", reason).strip()[:200], "agent"
    db.commit()
    return {"game": name, "status": row.status}
