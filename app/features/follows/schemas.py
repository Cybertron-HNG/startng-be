from datetime import datetime

from pydantic import BaseModel


class PlayerOut(BaseModel):
    """A player as others may see them: name and character only, never the email."""
    id: int
    name: str | None
    character: dict | None


class PeopleOut(BaseModel):
    count: int
    people: list[PlayerOut]


class FollowStateOut(BaseModel):
    user_id: int
    followers: int  # counts are public; the lists of names are not
    following: int
    you_follow: bool  # always false when signed out
    friends: bool  # you follow each other


class InviteOut(BaseModel):
    code: str
    path: str  # "/f/<code>": the website adds its own address in front
    expires_at: datetime


class InvitePreviewOut(BaseModel):
    inviter: PlayerOut
    expires_at: datetime


class AcceptedOut(BaseModel):
    friend: PlayerOut
