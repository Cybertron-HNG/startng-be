from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["family", "school", "class", "city", "friends"]


class CrewIn(BaseModel):
    name: str = Field(min_length=3, max_length=40)
    kind: Kind


class MemberOut(BaseModel):
    player_id: int
    name: str
    color: str | None
    owner: bool
    joined_at: datetime


class CrewInvite(BaseModel):
    """What anyone with the invite link sees."""
    code: str
    name: str
    kind: Kind
    owner: str
    members: int
    joined: bool
    full: bool


class CrewSummary(BaseModel):
    id: int
    name: str
    kind: Kind
    members: int
    mine: bool  # the signed-in player made it


class CrewOut(CrewSummary):
    """For members: the invite link's code and who is in."""
    code: str
    people: list[MemberOut]
