from datetime import datetime

from pydantic import BaseModel, Field, model_validator

SLUG = r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"


class JamOut(BaseModel):
    slug: str
    title: str
    brief: str
    opens_at: datetime
    entries_close_at: datetime
    voting_ends_at: datetime
    phase: str  # soon, open, voting, over
    entries: int


class EntryOut(BaseModel):
    game: str
    title: str
    url: str
    cover_url: str | None
    by: str
    votes: int | None  # shown once voting is over, so the count doesn't steer anyone
    winner: int | None  # 1, 2, 3
    voted: bool  # this player voted for it
    can_vote: bool
    why_not: str | None  # why this player can't vote for it now (sign in, play it first ...)


class JamPageOut(BaseModel):
    jam: JamOut
    entries: list[EntryOut]


class VoteIn(BaseModel):
    game: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")


class JamIn(BaseModel):
    slug: str = Field(pattern=SLUG)
    title: str = Field(min_length=3, max_length=80)
    brief: str = Field(min_length=10, max_length=400)
    opens_at: datetime
    entries_close_at: datetime
    voting_ends_at: datetime

    @model_validator(mode="after")
    def _in_order(self):
        if not self.opens_at < self.entries_close_at < self.voting_ends_at:
            raise ValueError("The dates must be in order: opens, entries close, voting ends")
        return self


class JamChange(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=80)
    brief: str | None = Field(default=None, min_length=10, max_length=400)
    opens_at: datetime | None = None
    entries_close_at: datetime | None = None
    voting_ends_at: datetime | None = None
