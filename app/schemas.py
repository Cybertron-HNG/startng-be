from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

Hex = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class GoogleIn(BaseModel):
    id_token: str


class CharacterStyle(BaseModel):
    # The look games build against. Colours are #rrggbb.
    primaryColor: str = Hex
    secondaryColor: str = Hex
    skinColor: str = Hex
    hairColor: str = Hex
    body: Literal["female", "male"] = "female"
    hair: Literal["short", "afro", "braids", "bun", "ponytail", "dreads", "bald"]
    hat: Literal["none", "cap", "beanie", "tophat", "crown"]
    accessory: Literal["none", "glasses", "earrings", "beard", "scarf", "backpack"]


class Character(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    style: CharacterStyle


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None = None
    character: Character | None = None

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class PublishIn(BaseModel):
    repo_url: str = Field(max_length=300)
    # The game's name and address (<name>.start.ng). Defaults to the repo name.
    name: str | None = Field(default=None, max_length=60)
    branch: str | None = Field(default=None, max_length=100)


class ProblemOut(BaseModel):
    """Something start.ng found while checking a game. code is for scripts, message for people."""

    code: str
    message: str
    file: str | None = None


class CheckOut(BaseModel):
    """POST /games/check: the game would go live like this."""

    ok: bool = True
    name: str
    url: str
    repo_url: str
    update: bool  # true when the repo is already live: publishing updates that game
    folder: str
    files: int
    size_bytes: int
    skipped: list[str]
    warnings: list[ProblemOut] = []


class UpdateProblem(BaseModel):
    """Why the latest push to a live game's repo didn't go live (the game kept its last version)."""

    commit: str
    at: datetime
    detail: str
    errors: list[ProblemOut] = []


class PublishOut(BaseModel):
    name: str
    url: str
    repo_url: str
    created: bool  # false when an existing game was updated
    folder: str  # where in the repo the game was found ("" = the top)
    files: int
    size_bytes: int
    skipped: list[str]  # files left out because the games host doesn't serve their type
    warnings: list[ProblemOut] = []  # things worth fixing that didn't stop the game going live


class PendingOut(BaseModel):
    """POST /games/publish without signing in, for a repo that isn't live yet."""

    status: str = "pending"
    name: str
    repo_url: str
    approve_url: str  # open it, sign in with start.ng, approve: the game goes live on your account
    expires_at: datetime
    folder: str
    files: int
    size_bytes: int
    skipped: list[str]
    warnings: list[ProblemOut] = []
    message: str


class PendingInfo(BaseModel):
    """What the approve page shows."""

    name: str
    repo_url: str
    expires_at: datetime
    available: bool  # false if the name was taken since; approving would fail


class GameOut(BaseModel):
    name: str
    url: str
    repo_url: str
    by: str
    updated_at: datetime
    cover_url: str | None = None  # the card picture; None means show the letter tile
    # Only in /games/mine: the latest push didn't go live, and why. None when it's all fine.
    update_problem: UpdateProblem | None = None
    # Only in /games/mine: how start.ng is keeping the game up to date from its repo.
    checked_at: datetime | None = None  # when the repo was last checked for a new push (None: not yet)
    live_commit: str | None = None  # the first 7 characters of the commit that's live (None: not known yet)
    updates_paused_until: datetime | None = None  # set while GitHub's limit has paused all checking
    # The game's leaderboard columns, or None when the game has no leaderboard (no startng.json, no scores)
    leaderboard: list[dict] | None = None


# Highest score a game can save; anything above is treated as a bad request.
MAX_SCORE = 1_000_000_000


class ScoreIn(BaseModel):
    # A plain score, for games without a startng.json ...
    score: int | None = Field(default=None, ge=0, le=MAX_SCORE)
    # ... or the game's own stats, exactly as its startng.json lists them: {"stage": 8, "kills": 100}
    stats: dict[str, Any] | None = None  # checked exactly by app/leaderboard.check_stats (numbers only)


class ScoreOut(BaseModel):
    best: int  # the player's best score in this game, after this one
    improved: bool  # true when this score is their new best
    rank: int  # their place on the game's leaderboard
    stats: dict[str, int | None]  # the best run's stats (games with a startng.json)


class LeaderboardEntry(BaseModel):
    rank: int
    player_id: int  # lets a game highlight the signed-in player's row
    name: str  # the player's character name
    color: str | None  # their character's top colour, for a dot next to the name
    score: int  # the first stat (the plain score for games without a startng.json)
    stats: dict[str, int | None]  # every stat, keyed like the game's startng.json


class PlayerOut(BaseModel):
    """What a game knows about its player: never the email."""

    id: int
    name: str
    style: CharacterStyle | None = None


class GameSessionOut(BaseModel):
    session: str  # send as "Authorization: Bearer <session>" to this game's player API
    expires_in: int  # seconds
    player: PlayerOut


# ---- Explore games (app/explore.py)
class ExploreGameOut(BaseModel):
    id: int
    name: str
    creator: str
    url: str
    host: str  # the site the game is on, so players see where they are going
    description: str
    cover_url: str  # a path on this API: /explore/games/<id>/cover?v=<version>
    created_at: datetime


class ExplorePendingOut(ExploreGameOut):
    status: str


class ExploreSubmitOut(BaseModel):
    game: ExploreGameOut
    status: str  # "approved": it is in the list now; "pending": a moderator has to approve it first
    manage_key: str  # shown once. With it the creator can remove the listing: DELETE /explore/games/<id>
