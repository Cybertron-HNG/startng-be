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
