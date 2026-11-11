"""Friends & follows (S2) routes. main.py mounts them with the "follows" flag: 404 while it's off."""
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import ApiError
from ...models import User
from ...security import current_user, optional_user
from . import service
from .schemas import AcceptedOut, FollowStateOut, InviteOut, InvitePreviewOut, PeopleOut, PlayerOut, QuickMessageIn, QuickMessageOut, QuickMessagesListOut, MuteOut

router = APIRouter(tags=["follows"])

_STATUS = {
    "player_not_found": status.HTTP_404_NOT_FOUND,
    "invite_not_found": status.HTTP_404_NOT_FOUND,
    "follow_limit": status.HTTP_429_TOO_MANY_REQUESTS,
    "rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
    "not_friends": status.HTTP_403_FORBIDDEN,
    "muted": status.HTTP_403_FORBIDDEN,
    "cant_message_self": status.HTTP_400_BAD_REQUEST,
    "invalid_preset": status.HTTP_400_BAD_REQUEST,
    "cant_mute_self": status.HTTP_400_BAD_REQUEST,
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

@router.post("/friends/messages", response_model=QuickMessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    payload: QuickMessageIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        msg = service.send_quick_message(
            db,
            user,
            payload.recipient_id,
            payload.preset,
            game_name=payload.game_name,
            challenge_code=payload.challenge_code,
        )
    except service.Refused as e:
        raise _refuse(e)
    return QuickMessageOut(
        id=msg.id,
        sender=_player(user),
        recipient_id=msg.recipient_id,
        preset=msg.preset,
        game_name=msg.game_name,
        challenge_code=msg.challenge_code,
        created_at=msg.created_at,
        read_at=msg.read_at,
    )


@router.get("/friends/messages", response_model=QuickMessagesListOut)
def list_messages(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    messages = service.get_quick_messages(db, user.id)
    senders = {u.id: _player(u) for u in service.people(db, {m.sender_id for m in messages})}
    out = [
        QuickMessageOut(
            id=m.id,
            sender=senders.get(m.sender_id, PlayerOut(id=m.sender_id, name="Player", character=None)),
            recipient_id=m.recipient_id,
            preset=m.preset,
            game_name=m.game_name,
            challenge_code=m.challenge_code,
            created_at=m.created_at,
            read_at=m.read_at,
        )
        for m in messages
    ]
    return QuickMessagesListOut(count=len(out), messages=out)


@router.post("/friends/messages/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    message_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    service.mark_message_read(db, user.id, message_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/friends/mute/{user_id}", response_model=MuteOut)
def mute_player(
    user_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        service.mute_user(db, user, user_id)
    except service.Refused as e:
        raise _refuse(e)
    return MuteOut(user_id=user_id, muted=True)


@router.delete("/friends/mute/{user_id}", response_model=MuteOut)
def unmute_player(
    user_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    service.unmute_user(db, user, user_id)
    return MuteOut(user_id=user_id, muted=False)


@router.get("/friends/muted", response_model=list[int])
def list_muted(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return sorted(service.get_muted_ids(db, user.id))

