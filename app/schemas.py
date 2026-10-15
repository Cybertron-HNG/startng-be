from datetime import datetime
from typing import Literal

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
