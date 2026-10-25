from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def session_lifetime(remember: bool) -> timedelta:
    """How long a sign-in lasts without a visit: a week, or a year for someone who chose to stay signed in."""
    return timedelta(days=settings.remember_days if remember else settings.session_days)


def create_token(user_id: int, remember: bool = False) -> str:
    exp = datetime.now(timezone.utc) + session_lifetime(remember)
    claims = {"sub": str(user_id), "exp": exp}
    if remember:
        claims["rem"] = 1  # stay signed in: the longer lifetime, kept every time the token is renewed
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def renewed_token(token: str, db: Session) -> str | None:
    """A fresh token for a sign-in that is past half of its life, so it lasts as long as the person keeps coming back: every visit
    in the week (or the year) pushes the end further out. None when the token is not a usable sign-in or has plenty of time left.
    A token from before this (24 hours, no "rem") is renewed at once into a week."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if claims.get("typ"):  # a live-room ticket or a game session, not a sign-in
            return None
        remember = bool(claims.get("rem"))
        left = datetime.fromtimestamp(claims["exp"], timezone.utc) - datetime.now(timezone.utc)
        if left > session_lifetime(remember) / 2:
            return None
        user = db.get(User, int(claims["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
    if user is None or user.blocked_at is not None:
        return None
    return create_token(user.id, remember)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds is None:
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
        # Live-room tickets (app/live.py) are signed with the same secret but only open a
        # game's live room; they never count as a sign-in.
        if payload.get("typ"):
            raise unauthorized
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or user.blocked_at is not None:  # blocked by an admin (app/admin.py)
        raise unauthorized
    return user


def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """The signed-in user, or None when no token was sent. A token that's sent must be valid."""
    return None if creds is None else current_user(creds, db)


def lenient_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Like optional_user, but a token that can't be used (expired, revoked, blocked) counts as no token. For routes that
    work for anyone and only do a little extra for a signed-in user, so a stale sign-in never costs the request."""
    try:
        return optional_user(creds, db)
    except HTTPException:
        return None


# ---- game sessions: what a game on <name>.start.ng uses to call the API for its player
GAME_SESSION_MINUTES = 120


def create_game_session(user_id: int, game: str) -> str:
    """A key that only works for one game's player API (scores, player). Never a sign-in."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=GAME_SESSION_MINUTES)
    return jwt.encode({"sub": str(user_id), "game": game, "typ": "game", "exp": exp}, settings.jwt_secret, algorithm="HS256")


def game_player(
    name: str,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """The player behind a request to /games/{name}/...: a game session for this game, or a
    start.ng sign-in (start.ng's own pages). Sessions for other games are refused."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds is None:
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
        typ = payload.get("typ")
        if typ == "game" and payload.get("game") != name:
            raise unauthorized
        if typ not in (None, "game"):  # e.g. live-room tickets
            raise unauthorized
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or user.blocked_at is not None:  # blocked by an admin (app/admin.py)
        raise unauthorized
    return user
