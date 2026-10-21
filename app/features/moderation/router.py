"""The name filter's admin API (start.ng/admin, the Name filter tab). 404 while the "moderation" flag is off."""
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...admin import AdminUser, log, require
from ...db import get_db
from ...models import Game, IndieGame, User
from . import service, words as defaults
from .models import ModerationWord
from .schemas import FlaggedName, TryIn, TryOut, WordIn, WordOut

router = APIRouter(prefix="/admin/moderation", tags=["moderation"])
manage = require("moderation.words")


@router.get("/words", response_model=list[WordOut])
def list_words(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Admins' words first (newest first), then the built-in lists."""
    own = [WordOut(id=w.id, word=w.word, kind=w.kind, strong=w.strong, built_in=False, added_by=w.added_by, created_at=w.created_at)
           for w in db.scalars(select(ModerationWord).order_by(ModerationWord.created_at.desc())).all()]
    built = [WordOut(id=None, word=w, kind="block", strong=True, built_in=True, added_by=None, created_at=None) for w in sorted(defaults.STRONG)]
    built += [WordOut(id=None, word=w, kind="block", strong=False, built_in=True, added_by=None, created_at=None) for w in sorted(defaults.WORDS)]
    built += [WordOut(id=None, word=w, kind="allow", strong=False, built_in=True, added_by=None, created_at=None) for w in sorted(defaults.ALLOWED)]
    return own + built


@router.post("/words", response_model=WordOut, status_code=status.HTTP_201_CREATED)
def add_word(body: WordIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Block a word, or allow one the built-in list catches by mistake. Letters only; look-alikes are handled."""
    found = service.tokens(body.word)
    if len(found) != 1 or not re.fullmatch(r"[a-z]{2,40}", found[0]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add one word, letters only")
    word = found[0]
    if db.scalar(select(ModerationWord.id).where(ModerationWord.word == word)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That word is already on the list")
    row = ModerationWord(word=word, kind=body.kind, strong=body.kind == "block" and body.strong, added_by=admin.email)
    db.add(row)
    log(db, admin, f"moderation.{body.kind}", word)
    db.commit()
    service.forget_cache()
    return WordOut(id=row.id, word=row.word, kind=row.kind, strong=row.strong, built_in=False, added_by=row.added_by, created_at=row.created_at)


@router.delete("/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_word(word_id: int, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    row = db.get(ModerationWord, word_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such word")
    log(db, admin, "moderation.remove", row.word, list=row.kind)
    db.delete(row)
    db.commit()
    service.forget_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/try", response_model=TryOut)
def try_text(body: TryIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Check a name the way start.ng would."""
    word = service.check_text(body.text, db)
    return TryOut(ok=word is None, word=word)


@router.get("/flagged", response_model=list[FlaggedName])
def flagged_names(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Names already on start.ng that fail the filter (from before it was on, or before a word was added)."""
    out: list[FlaggedName] = []
    for g in db.scalars(select(Game)).all():
        owner = g.owner.email if g.owner else None
        if word := service.check_text(g.name.replace("-", " "), db):
            out.append(FlaggedName(kind="game", name=g.name, word=word, target=g.name, owner=owner))
        if word := service.check_text(g.description, db):
            out.append(FlaggedName(kind="game_description", name=g.description or "", word=word, target=g.name, owner=owner))
    for u in db.scalars(select(User).where(User.character.is_not(None))).all():
        name = (u.character or {}).get("name")
        if word := service.check_text(name, db):
            out.append(FlaggedName(kind="character", name=name, word=word, target=str(u.id), owner=u.email))
    for i in db.scalars(select(IndieGame)).all():
        for text in (i.name, i.description):
            if word := service.check_text(text, db):
                out.append(FlaggedName(kind="creators_listing", name=i.name, word=word, target=str(i.id), owner=i.owner.email if i.owner else None))
                break
    return out
