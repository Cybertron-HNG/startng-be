from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token as g_id_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..activity import mark_seen, record
from ..config import settings
from ..features.moderation import service as moderation
from ..db import get_db
from .. import usernames
from ..models import User
from ..schemas import Character, GoogleIn, LoginIn, RegisterIn, TokenOut, UserOut, UsernameCheck, UsernameIn
from ..security import create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User) -> TokenOut:
    if user.blocked_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been blocked")
    return TokenOut(access_token=create_token(user.id), user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    usernames.ensure(db, user)
    record(db, email, "player", "account.created", email, via="email")
    db.commit()
    db.refresh(user)
    return _token_response(user)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _token_response(user)


@router.post("/google", response_model=TokenOut)
def google_login(body: GoogleIn, db: Session = Depends(get_db)):
    if not settings.google_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google auth is not configured")
    try:
        info = g_id_token.verify_oauth2_token(
            body.id_token, g_requests.Request(), settings.google_client_id
        )
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token")
    if not info.get("email_verified"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google email not verified")

    email = info["email"].lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=info.get("name"), google_sub=info["sub"])
        db.add(user)
        usernames.ensure(db, user)
        record(db, email, "player", "account.created", email, via="google")
    elif not user.google_sub:
        user.google_sub = info["sub"]
    db.commit()
    db.refresh(user)
    return _token_response(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    mark_seen(db, user)  # start.ng and its games call this on every visit
    if not user.username:  # an account from before usernames
        usernames.ensure(db, user)
        db.commit()
    return user


@router.put("/me/character", response_model=UserOut)
def save_character(body: Character, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if moderation.enforcing():
        moderation.require_clean(db, body.name, "Your character's name")
    user.character = body.model_dump()
    db.commit()
    db.refresh(user)
    return user


@router.put("/me/username", response_model=UserOut)
def change_username(body: UsernameIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Choose a new username (the address of their page). 422 when it breaks the rules, 409 when it's taken."""
    usernames.change(db, user, body.username)
    db.refresh(user)
    return user


@router.get("/usernames/{name}", response_model=UsernameCheck)
def check_username(name: str, db: Session = Depends(get_db)):
    """Whether a username is free and allowed, so the page can say so while someone types."""
    cleaned = usernames.clean(name)
    why = usernames.problem(db, cleaned)
    if why is None and usernames.is_taken(db, cleaned):
        why = "Someone already has that username."
    return UsernameCheck(username=cleaned, available=why is None, reason=why)
