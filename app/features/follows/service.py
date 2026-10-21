"""Friends & follows (S2). The functions other features may call (contract C6) are at the top:

    from app.features.follows import service as follows

    follows.following_of(db, user_id)   # set of user ids this player follows
    follows.followers_of(db, user_id)   # set of user ids who follow this player
    follows.friends_of(db, user_id)     # set of user ids who are friends (each follows the other)
    follows.is_friend(db, a_id, b_id)   # True when a and b follow each other
    follows.counts(db, user_id)         # {"followers": n, "following": n, "friends": n}

Accounts an admin has blocked (users.blocked_at) are left out of every list and count, and can't be followed.
Other features never import this feature's models; they call these functions instead.
"""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events
from ...models import User
from .models import Follow, FriendInvite

DAILY_FOLLOW_LIMIT = 100  # new follows per player in any 24 hours (accepting an invite doesn't count)
INVITE_DAYS = 7  # how long an invite link works
REUSE_INVITE_IF_DAYS_LEFT = 1  # hand back the player's current link while it has at least this long left


class Refused(Exception):
    """Something the player asked for that isn't allowed. code is the machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Contract C6: what other features call ----

def following_of(db: Session, user_id: int) -> set[int]:
    rows = db.scalars(
        select(Follow.target_user_id)
        .join(User, User.id == Follow.target_user_id)
        .where(Follow.follower_id == user_id, User.blocked_at.is_(None))
    )
    return set(rows)


def followers_of(db: Session, user_id: int) -> set[int]:
    rows = db.scalars(
        select(Follow.follower_id)
        .join(User, User.id == Follow.follower_id)
        .where(Follow.target_user_id == user_id, User.blocked_at.is_(None))
    )
    return set(rows)


def friends_of(db: Session, user_id: int) -> set[int]:
    return following_of(db, user_id) & followers_of(db, user_id)


def is_friend(db: Session, a_id: int, b_id: int) -> bool:
    if a_id == b_id:
        return False
    found = db.scalar(
        select(func.count()).select_from(Follow).where(
            ((Follow.follower_id == a_id) & (Follow.target_user_id == b_id))
            | ((Follow.follower_id == b_id) & (Follow.target_user_id == a_id))
        )
    )
    return found == 2


def counts(db: Session, user_id: int) -> dict[str, int]:
    following = following_of(db, user_id)
    followers = followers_of(db, user_id)
    return {"followers": len(followers), "following": len(following), "friends": len(following & followers)}


# ---- Used by this feature's routes ----

def people(db: Session, ids: set[int]) -> list[User]:
    """The players behind a set of ids, by name (blocked accounts are already left out of the sets above)."""
    if not ids:
        return []
    users = db.scalars(select(User).where(User.id.in_(ids))).all()
    return sorted(users, key=lambda u: ((u.name or "").lower(), u.id))


def _target(db: Session, follower: User, target_id: int) -> User:
    if target_id == follower.id:
        raise Refused("cant_follow_self", "You can't follow yourself.")
    target = db.get(User, target_id)
    if target is None or target.blocked_at is not None:
        raise Refused("player_not_found", "That player wasn't found.")
    return target


def _follows(db: Session, follower_id: int, target_id: int) -> bool:
    return db.scalar(
        select(func.count()).select_from(Follow)
        .where(Follow.follower_id == follower_id, Follow.target_user_id == target_id)
    ) == 1


def follow(db: Session, follower: User, target_id: int, at: datetime | None = None) -> bool:
    """Follow a player. True when it's new, False when they already followed (not an error)."""
    at = at or _now()
    _target(db, follower, target_id)
    if _follows(db, follower.id, target_id):
        return False
    made = db.scalar(
        select(func.count()).select_from(Follow)
        .where(Follow.follower_id == follower.id, Follow.created_at >= at - timedelta(days=1))
    ) or 0
    if made >= DAILY_FOLLOW_LIMIT:
        raise Refused("follow_limit", f"You can follow up to {DAILY_FOLLOW_LIMIT} players a day. Try again tomorrow.")
    db.add(Follow(follower_id=follower.id, target_user_id=target_id, created_at=at))
    try:
        db.commit()
    except IntegrityError:  # the same follow arrived twice at once
        db.rollback()
        return False
    events.publish("follow_created", {"follower_id": follower.id, "target_user_id": target_id})
    return True


def unfollow(db: Session, follower: User, target_id: int) -> bool:
    """Stop following. True when there was a follow to remove. Ends a friendship too."""
    gone = db.execute(
        delete(Follow).where(Follow.follower_id == follower.id, Follow.target_user_id == target_id)
    ).rowcount
    db.commit()
    return bool(gone)


def make_invite(db: Session, inviter: User, at: datetime | None = None) -> FriendInvite:
    """The player's invite link: their current one while it has a day or more left, otherwise a new one."""
    at = at or _now()
    current = db.scalar(
        select(FriendInvite)
        .where(
            FriendInvite.inviter_id == inviter.id,
            FriendInvite.expires_at > at + timedelta(days=REUSE_INVITE_IF_DAYS_LEFT),
        )
        .order_by(FriendInvite.expires_at.desc())
        .limit(1)
    )
    if current is not None:
        return current
    invite = FriendInvite(
        code=secrets.token_urlsafe(8), inviter_id=inviter.id, created_at=at, expires_at=at + timedelta(days=INVITE_DAYS)
    )
    db.add(invite)
    db.commit()
    return invite


def _open_invite(db: Session, code: str, at: datetime) -> tuple[FriendInvite, User]:
    invite = db.scalar(select(FriendInvite).where(FriendInvite.code == code, FriendInvite.expires_at > at))
    inviter = db.get(User, invite.inviter_id) if invite is not None else None
    if invite is None or inviter is None or inviter.blocked_at is not None:
        raise Refused("invite_not_found", "This invite link has expired or doesn't exist. Ask your friend for a new one.")
    return invite, inviter


def invite_preview(db: Session, code: str, at: datetime | None = None) -> User:
    """Who sent this invite (for the page that asks "Accept?"). Only name and character are shown."""
    _, inviter = _open_invite(db, code, at or _now())
    return inviter


def accept_invite(db: Session, user: User, code: str, at: datetime | None = None) -> User:
    """Make the player and the inviter friends (both follow each other). Returns the inviter."""
    at = at or _now()
    _, inviter = _open_invite(db, code, at)
    if inviter.id == user.id:
        raise Refused("own_invite", "This is your own invite link. Send it to a friend instead.")
    new = []
    for a, b in ((user.id, inviter.id), (inviter.id, user.id)):
        if not _follows(db, a, b):
            db.add(Follow(follower_id=a, target_user_id=b, created_at=at))
            new.append((a, b))
    try:
        db.commit()
    except IntegrityError:  # accepted twice at once; the other request made the follows
        db.rollback()
        return inviter
    for a, b in new:
        events.publish("follow_created", {"follower_id": a, "target_user_id": b})
    return inviter
