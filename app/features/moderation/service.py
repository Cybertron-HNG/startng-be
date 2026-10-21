"""The name filter (H2). Other features call this to keep offensive words out of names and text people see:

    from app.features.moderation.service import check_text, require_clean

    bad = check_text("Fuck Racer", db)        # "fuck" (the word it found), or None when the text is fine
    require_clean(db, crew.name, "Crew name")  # raises a 422 the website shows as is

It uses the default lists in words.py plus what admins added on the admin page. Whether start.ng's own names
(games, characters, Creators listings) are checked follows the "moderation" flag: see enforcing().
"""
import re
import time
import unicodedata

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import flags
from ...db import SessionLocal
from ...errors import ApiError
from . import words as defaults
from .models import ModerationWord

FLAG = "moderation"
ENDINGS = ("", "s", "es", "ed", "ing")  # not -y or -er: "spicy" is not "spic"
# Look-alike characters people use to get round a filter.
LOOKALIKES = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "i"})
CACHE_SECONDS = 30

_cache: dict = {"at": -1e9, "lists": None}


def enforcing() -> bool:
    """Whether start.ng checks its own names (games, characters, Creators). Other features may call check_text
    whatever this says."""
    return flags.is_on(FLAG)


def _squash(s: str) -> str:
    """Letters repeated three or more times count once: shiiit -> shit."""
    return re.sub(r"(.)\1{2,}", r"\1", s)


def tokens(text: str) -> list[str]:
    """The words in a text after undoing look-alikes and accents. Runs of single letters (s.h.i.t, f u c k) are
    joined into one word."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().translate(LOOKALIKES)
    out, letters = [], []
    for t in re.findall(r"[a-z]+", plain):
        if len(t) == 1:
            letters.append(t)
            continue
        if len(letters) > 1:
            out.append("".join(letters))
        letters = []
        out.append(t)
    if len(letters) > 1:
        out.append("".join(letters))
    return out


def word_lists(db: Session | None = None) -> tuple[set[str], set[str], set[str]]:
    """(strong, whole words, allowed): the defaults plus admins' words. Cached for CACHE_SECONDS."""
    if _cache["lists"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["lists"]
    own = db is None
    db = db or SessionLocal()
    try:
        rows = db.scalars(select(ModerationWord)).all()
    finally:
        if own:
            db.close()
    strong, whole, allowed = set(defaults.STRONG), set(defaults.WORDS), set(defaults.ALLOWED)
    for r in rows:
        if r.kind == "allow":
            allowed.add(r.word)
        elif r.strong:
            strong.add(r.word)
        else:
            whole.add(r.word)
    # An allowed word wins over the default lists (an admin decided it's fine here).
    lists = (strong - allowed, whole - allowed, allowed)
    _cache.update(at=time.monotonic(), lists=lists)
    return lists


def forget_cache() -> None:
    """Admins changed the lists: use them straight away."""
    _cache.update(at=-1e9, lists=None)


def check_text(text: str | None, db: Session | None = None) -> str | None:
    """The first blocked word in the text, or None when it's fine."""
    if not text:
        return None
    strong, whole, allowed = word_lists(db)
    for token in tokens(text):
        if token in allowed:
            continue
        for t in {token, _squash(token)}:
            if any(t == w + e for w in whole for e in ENDINGS):
                return next(w for w in whole if any(t == w + e for e in ENDINGS))
            hit = next((w for w in strong if w in t), None)
            if hit:
                return hit
    return None


def require_clean(db: Session | None, text: str | None, field: str) -> None:
    """Raise a 422 (code text_not_allowed) when the text has a blocked word. The message names the field, not the word."""
    if check_text(text, db):
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "text_not_allowed",
                       f"{field} has a word that isn't allowed on start.ng. Please change it.")
