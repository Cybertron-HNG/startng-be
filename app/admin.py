"""Admins, roles and what they can do.

Super admins are the emails in settings.super_admin_emails: they have every permission and are the only ones who
add admins and make roles. Other admins are rows in `admins` (added by email), each with a role: a named set of
PERMISSIONS. There's no admin sign-up: an admin signs in to start.ng with Google as that email.

Only a Google sign-in counts. Anyone can register an email with a password (POST /auth/register) without
proving they own it, so an account that has a password never gets admin access, even if its email is on the list.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .activity import record
from .models import Admin, Game, User
from .security import current_user

# Every permission a role can hold, with what it lets an admin do (shown when making a role).
PERMISSIONS = {
    "reports.view": "See reported and flagged games",
    "reports.close": "Approve reports (which hides the game) or dismiss them, and put hidden games back",
    "games.flag": "Flag a game, which hides it",
    "games.covers": "Make card-sized copies of games' own covers (once, after an update)",
    "developers.contact": "Record reaching out to a developer about an issue",
    "developers.block": "Block a developer contacted about enough issues (hides their games)",
    "developers.delete": "Delete a developer contacted about enough issues, with their games",
    "tournaments.view": "See the current and next tournament before players do",
    "tournaments.edit": "Change the games of a tournament that hasn't started",
    "log.view": "See the admin activity log",
}

def register_permissions(perms: dict[str, str]) -> None:
    """A feature adds its own admin permissions (key: what it lets an admin do). Keys must be unique."""
    for key, label in perms.items():
        if key in PERMISSIONS and PERMISSIONS[key] != label:
            raise ValueError(f'Admin permission "{key}" is already used by another feature')
        PERMISSIONS[key] = label


# Why a game is hidden (games.hidden), weakest first. A stronger reason replaces a weaker one. Features add their
# own with register_hide_reason (a short word, which Your games shows the owner).
REPORTED, FLAGGED, BLOCKED = "reported", "flagged", "blocked"
HIDE_REASONS: list[str] = [REPORTED, FLAGGED, BLOCKED]


def register_hide_reason(reason: str, below: str = FLAGGED) -> None:
    """A feature's own reason for hiding a game, ranked just below another (by default below an admin's flag)."""
    if reason not in HIDE_REASONS:
        HIDE_REASONS.insert(HIDE_REASONS.index(below), reason)


def now() -> datetime:
    return datetime.now(timezone.utc)


def super_admins() -> set[str]:
    return {e.strip().lower() for e in settings.super_admin_emails.split(",") if e.strip()}


@dataclass
class AdminUser:
    user: User
    email: str
    is_super: bool
    role: str | None
    permissions: set[str]

    def can(self, permission: str) -> bool:
        return self.is_super or permission in self.permissions


def admin_of(db: Session, user: User) -> AdminUser | None:
    """The user's admin access, or None. Needs a Google sign-in with no password, and not blocked."""
    if user.blocked_at is not None or not user.google_sub or user.password_hash:
        return None
    email = user.email.lower()
    if email in super_admins():
        return AdminUser(user, email, True, "Super admin", set(PERMISSIONS))
    row = db.scalar(select(Admin).where(Admin.email == email))
    if row is None:
        return None
    perms = {p for p in (row.role.permissions if row.role else []) if p in PERMISSIONS}
    return AdminUser(user, email, False, row.role.name if row.role else None, perms)


def current_admin(user: User = Depends(current_user), db: Session = Depends(get_db)) -> AdminUser:
    """Any admin. Others get a 404, so the admin API doesn't show it exists."""
    admin = admin_of(db, user)
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return admin


def require(permission: str):
    def dependency(admin: AdminUser = Depends(current_admin)) -> AdminUser:
        if not admin.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your admin role can't do this")
        return admin
    return dependency


def require_super(admin: AdminUser = Depends(current_admin)) -> AdminUser:
    if not admin.is_super:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can do this")
    return admin


def log(db: Session, admin: AdminUser | str, action: str, target: str | None = None, **detail) -> None:
    """Add an admin's action to the activity log. The caller commits."""
    record(db, admin if isinstance(admin, str) else admin.email, "admin", action, target, **detail)


def hide(game: Game, why: str) -> None:
    """Mark a game hidden (the caller commits). A stronger reason replaces a weaker one (HIDE_REASONS). Features
    use hide_game, which also takes it offline and out of tournaments."""
    order = [None, *HIDE_REASONS]
    rank = order.index(game.hidden) if game.hidden in order else 0
    if order.index(why) > rank:
        game.hidden = why
        game.hidden_at = now()


def unhide(game: Game) -> None:
    game.hidden = None
    game.hidden_at = None


def set_offline(store, name: str, hidden: bool) -> None:
    """Take a game's own address offline (the Worker shows an error page) or back online. Without game storage
    (local testing) there's nothing to change. A storage hiccup is logged, never undoing an admin's decision."""
    if store is None:
        return
    try:
        store.set_hidden(name, hidden)
    except Exception:
        logging.getLogger(__name__).exception("Couldn't %s %s", "take offline" if hidden else "put back online", name)


def hide_game(db: Session, store, game: Game, why: str) -> None:
    """Hide a game everywhere: off start.ng's lists and pages, offline at its own address, out of tournaments that
    haven't started and the one that's running. Commits."""
    from . import tournaments  # here, not at the top: tournaments doesn't need admin, admin rarely needs it

    hide(game, why)
    db.commit()
    set_offline(store, game.name, True)
    tournaments.replace_hidden(db, game.name)


def show_game(db: Session, store, game: Game) -> None:
    """Put a hidden game back everywhere (the caller has decided it may come back). Commits."""
    unhide(game)
    db.commit()
    set_offline(store, game.name, False)
