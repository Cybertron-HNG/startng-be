"""Friends & follows (S2) routes. main.py mounts them with the "follows" flag: 404 while it's off."""
from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import ApiError
from ...models import Game, User
from ...routers.games import _game_out
from ...security import current_user, optional_user
from . import service
from .schemas import AcceptedOut, FollowingGamesOut, FollowStateOut, GameCreatorOut, InviteOut, InvitePreviewOut, PeopleOut, PlayerOut

router = APIRouter(tags=["follows"])

_STATUS = {
    "player_not_found": status.HTTP_404_NOT_FOUND,
    "invite_not_found": status.HTTP_404_NOT_FOUND,
    "follow_limit": status.HTTP_429_TOO_MANY_REQUESTS,
}


def _refuse(e: service.Refused) -> ApiError:
    return ApiError(_STATUS.get(e.code, status.HTTP_400_BAD_REQUEST), e.code, e.message)


def _player(u: User) -> PlayerOut:
    return PlayerOut(id=u.id, name=u.name, character=u.character)


def _people(db: Session, ids: set[int]) -> PeopleOut:
    people = service.people(db, ids)
    return PeopleOut(count=len(people), people=[_player(u) for u in people])


def _state(db: Session, target_id: int, viewer: User | None) -> FollowStateOut:
    c = service.counts(db, target_id)
    you_follow = viewer is not None and target_id in service.following_of(db, viewer.id)
    friends = viewer is not None and service.is_friend(db, viewer.id, target_id)
    return FollowStateOut(
        user_id=target_id, followers=c["followers"], following=c["following"], you_follow=you_follow, friends=friends
    )


@router.put("/follows/{user_id}", response_model=FollowStateOut)
def follow(user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        service.follow(db, user, user_id)
    except service.Refused as e:
        raise _refuse(e)
    return _state(db, user_id, user)


@router.delete("/follows/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def unfollow(user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    service.unfollow(db, user, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/follows/{user_id}", response_model=FollowStateOut)
def follow_state(user_id: int, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None or target.blocked_at is not None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "player_not_found", "That player wasn't found.")
    return _state(db, user_id, user)


@router.get("/follows/game/{name}", response_model=GameCreatorOut)
def game_creator(name: str = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"), user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """Who made a game and whether you follow them, for the Follow button on the game's page (Ziki #42)."""
    game = db.get(Game, name)
    owner = game.owner if game is not None else None
    if game is None or game.hidden is not None or owner is None or owner.blocked_at is not None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "game_not_found", "That game wasn't found.")
    return GameCreatorOut(game=game.name, creator=_player(owner), state=_state(db, owner.id, user))


FOLLOWING_GAMES = 8  # the homepage row shows one line of cards


@router.get("/me/following/games", response_model=FollowingGamesOut)
def following_games(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The newest listed games by the creators you follow, newest first (Ziki #42)."""
    ids = service.following_of(db, user.id)
    if not ids:
        return FollowingGamesOut(following=0, games=[])
    games = db.scalars(
        select(Game).where(Game.owner_id.in_(ids), Game.hidden.is_(None)).order_by(Game.created_at.desc()).limit(FOLLOWING_GAMES)
    ).all()
    return FollowingGamesOut(following=len(ids), games=[_game_out(g) for g in games])


@router.get("/me/following", response_model=PeopleOut)
def my_following(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _people(db, service.following_of(db, user.id))


@router.get("/me/followers", response_model=PeopleOut)
def my_followers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _people(db, service.followers_of(db, user.id))


@router.get("/me/friends", response_model=PeopleOut)
def my_friends(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _people(db, service.friends_of(db, user.id))


@router.post("/friend-invites", response_model=InviteOut)
def make_invite(user: User = Depends(current_user), db: Session = Depends(get_db)):
    invite = service.make_invite(db, user)
    return InviteOut(code=invite.code, path=f"/f/{invite.code}", expires_at=invite.expires_at)


@router.get("/friend-invites/{code}", response_model=InvitePreviewOut)
def preview_invite(code: str, db: Session = Depends(get_db)):
    try:
        invite, inviter = service._open_invite(db, code, service._now())
    except service.Refused as e:
        raise _refuse(e)
    return InvitePreviewOut(inviter=_player(inviter), expires_at=invite.expires_at)


@router.post("/friend-invites/{code}/accept", response_model=AcceptedOut)
def accept_invite(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        inviter = service.accept_invite(db, user, code)
    except service.Refused as e:
        raise _refuse(e)
    return AcceptedOut(friend=_player(inviter))
