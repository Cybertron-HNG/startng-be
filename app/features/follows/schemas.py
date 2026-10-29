from datetime import datetime

from pydantic import BaseModel

from ...schemas import GameOut


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


class GameCreatorOut(BaseModel):
    """A game's creator, for the Follow button on the game's page."""
    game: str
    creator: PlayerOut
    state: FollowStateOut  # you_follow is false when signed out, and for your own game you can't follow


class FollowingGamesOut(BaseModel):
    """The newest games from creators you follow, for the homepage."""
    following: int  # how many creators you follow (0: the homepage shows nothing)
    games: list[GameOut]
