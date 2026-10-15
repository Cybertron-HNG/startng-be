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


class PublishOut(BaseModel):
    name: str
    url: str
    repo_url: str
    created: bool  # false when an existing game was updated
    folder: str  # where in the repo the game was found ("" = the top)
    files: int
    size_bytes: int
    skipped: list[str]  # files left out because the games host doesn't serve their type


class GameOut(BaseModel):
    name: str
    url: str
    repo_url: str
    by: str
    updated_at: datetime
    cover_url: str | None = None  # the card picture; None means show the letter tile
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
