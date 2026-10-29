from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ChallengeIn(BaseModel):
    """One of: run_id (a run of yours, e.g. from the Kit's end-of-round card) or game (your best run on it, from the
    game's page). Either way the score is read from the run log, never sent."""
    run_id: int | None = None
    game: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")

    @model_validator(mode="after")
    def one_of(self):
        if (self.run_id is None) == (self.game is None):
            raise ValueError("Send run_id or game")
        return self


class PlayerOut(BaseModel):
    id: int
    name: str
    character: dict | None  # for drawing their character; never the email


class GameOut(BaseModel):
    name: str
    title: str
    url: str
    cover_url: str | None
    stat: dict  # the column the challenge is about: {key, label, order, format}


class AttemptOut(BaseModel):
    player: PlayerOut
    best: int | None  # None: no run since taking it on
    best_text: str | None
    beaten: bool
    short_by: int | None  # how far from the target (None once beaten, or before a run)


class CanOut(BaseModel):
    can: bool  # the player has a run on the game to challenge with


class ChallengeOut(BaseModel):
    code: str
    url: str  # start.ng/c/<code>
    challenger: PlayerOut
    game: GameOut
    score: int
    score_text: str  # 4,200 or 3:05, as the boards show it
    created_at: datetime
    expires_at: datetime
    mine: bool  # the viewer made it
    me: AttemptOut | None  # the viewer's own attempt, once they've taken it on
    attempts: list[AttemptOut]  # everyone who took it on, best first (only for the player who made it)
    takers: int
