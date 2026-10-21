from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import ApiError
from ...models import User
from ...routers.games import _player_name
from ...security import current_user, optional_user
from . import service
from .models import Crew
from .schemas import CrewIn, CrewInvite, CrewOut, CrewSummary, MemberOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/crews", tags=["crews"])

CODE = Path(pattern=r"^[\w-]{8,16}$")


def _color(user: User) -> str | None:
    return ((user.character or {}).get("style") or {}).get("primaryColor")


def _summary(db: Session, crew: Crew, user: User) -> CrewSummary:
    return CrewSummary(id=crew.id, name=crew.name, kind=crew.kind, members=service.member_count(db, crew.id), mine=crew.owner_id == user.id)


def _refuse(e: service.Refused) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, e.code, e.message)


def _crew(db: Session, crew_id: int) -> Crew:
    crew = service.find(db, crew_id)
    if crew is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no crew here")
    return crew


def _members_only(db: Session, crew_id: int, user: User) -> Crew:
    """A crew is private: to anyone who isn't in it, it doesn't exist."""
    crew = _crew(db, crew_id)
    if not service.is_member(db, crew.id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no crew here")
    return crew


@router.post("", response_model=CrewOut, status_code=status.HTTP_201_CREATED)
def create(body: CrewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        crew = service.create(db, user, body.name, body.kind)
    except service.Refused as e:
        raise _refuse(e)
    return _out(db, crew, user)


@router.get("/mine", response_model=list[CrewSummary])
def my_crews(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_summary(db, c, user) for c in service.mine(db, user)]


@router.get("/join/{code}", response_model=CrewInvite)
def invite(code: str = CODE, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """What the invite link shows before joining."""
    crew = service.find_by_code(db, code)
    if crew is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no crew at this link")
    count = service.member_count(db, crew.id)
    return CrewInvite(code=crew.code, name=crew.name, kind=crew.kind, owner=_player_name(crew.owner), members=count,
                      joined=bool(user) and service.is_member(db, crew.id, user.id), full=count >= service.MAX_MEMBERS)


@router.post("/join/{code}", response_model=CrewOut)
def join(code: str = CODE, user: User = Depends(current_user), db: Session = Depends(get_db)):
    crew = service.find_by_code(db, code)
    if crew is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no crew at this link")
    try:
        service.join(db, crew, user)
    except service.Refused as e:
        raise _refuse(e)
    return _out(db, crew, user)


def _out(db: Session, crew: Crew, user: User) -> CrewOut:
    people = [MemberOut(player_id=m.user_id, name=_player_name(m.user), color=_color(m.user), owner=m.user_id == crew.owner_id,
                        joined_at=m.joined_at) for m in crew.members]
    return CrewOut(id=crew.id, name=crew.name, kind=crew.kind, members=len(people), mine=crew.owner_id == user.id, code=crew.code, people=people)


@router.get("/{crew_id}", response_model=CrewOut)
def get_one(crew_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, _members_only(db, crew_id, user), user)


@router.delete("/{crew_id}/members/me", status_code=status.HTTP_204_NO_CONTENT)
def leave(crew_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        service.leave(db, _members_only(db, crew_id, user), user)
    except service.Refused as e:
        raise _refuse(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{crew_id}/members/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(crew_id: int, player_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    crew = _members_only(db, crew_id, user)
    if crew.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the player who made the crew can remove people")
    try:
        service.remove(db, crew, user, player_id)
    except service.Refused as e:
        raise _refuse(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{crew_id}/new-link", response_model=CrewOut)
def new_link(crew_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    crew = _members_only(db, crew_id, user)
    if crew.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the player who made the crew can change its link")
    service.new_code(db, crew, user)
    return _out(db, crew, user)


@router.delete("/{crew_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(crew_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    crew = _members_only(db, crew_id, user)
    if crew.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the player who made the crew can delete it")
    db.delete(crew)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
