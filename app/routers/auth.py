from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token as g_id_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import User
from ..schemas import GoogleIn, LoginIn, RegisterIn, TokenOut, UserOut
from ..security import create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User) -> TokenOut:
    return TokenOut(access_token=create_token(user.id), user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
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
    elif not user.google_sub:
        user.google_sub = info["sub"]
    db.commit()
    db.refresh(user)
    return _token_response(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
