"""Crews: the rules, and what other features may call (this feature's contract).

A player makes a crew (a name and a kind) and shares its invite link. Whoever opens the link and joins is in. A crew holds
up to MAX_MEMBERS people and a player can be in up to MAX_CREWS crews. The maker removes members or deletes the crew; anyone
else just leaves. Other features (private tournaments, scoped leaderboards) call `crew_ids_of`, `members_of` and `is_member`
and never read these tables.
"""
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...activity import record as log_activity
from ...models import User
from .models import Crew, CrewMember

MAX_MEMBERS = 200
MAX_CREWS = 5  # crews a player can be in (including the ones they made)


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def find(db: Session, crew_id: int) -> Crew | None:
    return db.get(Crew, crew_id)


def find_by_code(db: Session, code: str) -> Crew | None:
    return db.scalar(select(Crew).where(Crew.code == code))


def is_member(db: Session, crew_id: int, user_id: int) -> bool:
    return db.scalar(select(CrewMember.id).where(CrewMember.crew_id == crew_id, CrewMember.user_id == user_id)) is not None


def member_count(db: Session, crew_id: int) -> int:
    return db.scalar(select(func.count()).select_from(CrewMember).where(CrewMember.crew_id == crew_id)) or 0


def crew_ids_of(db: Session, user_id: int) -> list[int]:
    """The crews a player is in."""
    return list(db.scalars(select(CrewMember.crew_id).where(CrewMember.user_id == user_id)).all())


def members_of(db: Session, crew_id: int) -> list[int]:
    """The player ids in a crew."""
    return list(db.scalars(select(CrewMember.user_id).where(CrewMember.crew_id == crew_id)).all())


def create(db: Session, owner: User, name: str, kind: str) -> Crew:
    if len(crew_ids_of(db, owner.id)) >= MAX_CREWS:
        raise Refused("too_many", f"You can be in {MAX_CREWS} crews. Leave one to make another.")
    crew = Crew(code=secrets.token_urlsafe(8), owner_id=owner.id, name=name.strip(), kind=kind, members=[CrewMember(user_id=owner.id)])
    db.add(crew)
    log_activity(db, owner, "player", "crew.created", crew.code, crew_kind=kind)
    db.commit()
    return crew


def join(db: Session, crew: Crew, user: User) -> None:
    """Join by the invite link. Joining twice is fine."""
    if is_member(db, crew.id, user.id):
        return
    if member_count(db, crew.id) >= MAX_MEMBERS:
        raise Refused("full", "This crew is full.")
    if len(crew_ids_of(db, user.id)) >= MAX_CREWS:
        raise Refused("too_many", f"You are in {MAX_CREWS} crews already. Leave one to join this.")
    db.add(CrewMember(crew_id=crew.id, user_id=user.id))
    try:
        db.commit()
    except IntegrityError:  # joined twice at once
        db.rollback()


def leave(db: Session, crew: Crew, user: User) -> None:
    if crew.owner_id == user.id:
        raise Refused("owner", "You made this crew. Delete it instead.")
    db.query(CrewMember).filter(CrewMember.crew_id == crew.id, CrewMember.user_id == user.id).delete()
    db.commit()


def remove(db: Session, crew: Crew, owner: User, player_id: int) -> None:
    """The maker removes a member."""
    if player_id == crew.owner_id:
        raise Refused("owner", "The maker can't be removed. Delete the crew instead.")
    db.query(CrewMember).filter(CrewMember.crew_id == crew.id, CrewMember.user_id == player_id).delete()
    log_activity(db, owner, "player", "crew.member_removed", crew.code, player_id=player_id)
    db.commit()


def new_code(db: Session, crew: Crew, owner: User) -> None:
    """A new invite link: the old one stops working, members stay."""
    crew.code = secrets.token_urlsafe(8)
    log_activity(db, owner, "player", "crew.link_changed", crew.code)
    db.commit()


def mine(db: Session, user: User) -> list[Crew]:
    return list(db.scalars(select(Crew).join(CrewMember).where(CrewMember.user_id == user.id).order_by(Crew.name)).all())
