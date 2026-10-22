"""Usernames: a short, unique handle for every player, used in their page address (start.ng/p/<username>).

Everyone gets a default one when their account is made (from their name, with a few digits when it's taken), and can
change it to anything free that follows the rules below. Names are stored in lowercase, so "Ada" and "ada" are the same.
"""
import re
import secrets

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import User

MIN, MAX = 3, 24
PATTERN = re.compile(r"^[a-z0-9_]+$")
# Words that would look official or clash with addresses on the site.
RESERVED = {
    "admin", "administrator", "root", "support", "help", "staff", "team", "moderator", "mod", "start", "startng", "start_ng",
    "api", "www", "me", "you", "null", "undefined", "system", "official", "security", "billing", "hangout", "games",
    "tournament", "creators", "docs", "shop", "wardrobe", "profile", "settings", "login", "logout", "signin", "signup",
}


def clean(raw: str | None) -> str:
    """What the player typed, trimmed and lowercased (a leading @ is fine)."""
    return (raw or "").strip().lstrip("@").lower()


def problem(db: Session | None, name: str) -> str | None:
    """Why this (already cleaned) username can't be used, or None when it follows the rules. Doesn't check it's free."""
    if not (MIN <= len(name) <= MAX):
        return f"Use {MIN} to {MAX} characters."
    if not PATTERN.match(name):
        return "Use only letters, numbers and underscores."
    if not re.search(r"[a-z]", name):
        return "Include at least one letter."
    if name in RESERVED:
        return "That name is kept for start.ng."
    from .features.moderation import service as moderation  # the name filter, whether or not start.ng's own names are enforced

    if moderation.check_text(name.replace("_", " "), db):
        return "That name has a word that isn't allowed on start.ng."
    return None


def is_taken(db: Session, name: str, except_user_id: int | None = None) -> bool:
    found = db.scalar(select(User.id).where(User.username == name))
    return found is not None and found != except_user_id


def base_for(user: User) -> str:
    """A starting point for their default: their character's name, else their name, else the start of their email."""
    raw = (user.character or {}).get("name") or user.name or (user.email or "").split("@")[0] or "player"
    base = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[: MAX - 5].strip("_")
    if not re.search(r"[a-z]", base):
        base = f"player_{base}" if base else "player"
    while len(base) < MIN:
        base += "x"
    return base


def default_for(db: Session, user: User) -> str:
    """A free username that follows the rules: their name, then the name with digits until one is free."""
    base = base_for(user)
    if problem(db, base) is not None:
        base = "player"
    candidate = base
    for attempt in range(60):
        if problem(db, candidate) is None and not is_taken(db, candidate, user.id):
            return candidate
        candidate = f"{base}{secrets.randbelow(10 ** (3 + attempt // 20)):0{3 + attempt // 20}d}"
    return f"player{secrets.token_hex(6)}"


def ensure(db: Session, user: User) -> str:
    """The player's username, making their default first when they have none yet. The caller commits."""
    if not user.username:
        user.username = default_for(db, user)
    return user.username


def change(db: Session, user: User, raw: str) -> str:
    """Give the player a new username. A 422 when it breaks the rules, a 409 when someone has it."""
    name = clean(raw)
    why = problem(db, name)
    if why:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "username_invalid", why)
    if is_taken(db, name, user.id):
        raise ApiError(status.HTTP_409_CONFLICT, "username_taken", "Someone already has that username.")
    user.username = name
    try:
        db.commit()
    except IntegrityError:  # two people chose it at the same moment
        db.rollback()
        raise ApiError(status.HTTP_409_CONFLICT, "username_taken", "Someone already has that username.")
    return name
