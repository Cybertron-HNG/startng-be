from datetime import datetime

from pydantic import BaseModel, Field


class ItemOut(BaseModel):
    key: str
    name: str
    slot: str
    rarity: str
    how: str
    price: int | None  # today's shop price; None = not in the shop today
    owned_share: float  # 0 to 1: the share of players who own it


class CatalogItemOut(ItemOut):
    owned: bool = False


class EntryOut(BaseModel):
    amount: int
    reason: str
    ref: str
    at: datetime


class WalletOut(BaseModel):
    balance: int
    earned_today: int  # from playing, towards daily_cap
    daily_cap: int
    entries: list[EntryOut]


class OwnedOut(BaseModel):
    key: str
    source: str


class WardrobeOut(BaseModel):
    owned: list[OwnedOut]
    worn: dict[str, str]  # slot -> item
    style: dict  # what games get: the hat/accessory to use and every worn item


class PublicWardrobeOut(BaseModel):
    worn: dict[str, str]
    showcase: list[str]  # the player's three rarest items
    items_owned: int


class ShopOut(BaseModel):
    day: str
    featured: list[ItemOut]
    items: list[ItemOut]


class ItemIn(BaseModel):
    item: str = Field(min_length=1, max_length=64)


class SlotIn(BaseModel):
    slot: str = Field(min_length=1, max_length=16)


class BuyOut(BaseModel):
    balance: int
    item: str


class GoalOut(BaseModel):
    key: str
    label: str
    stat: str
    at_least: int | None
    at_most: int | None
    repeat: str
    tier: str  # new, easy, medium, hard, very_hard, legendary
    coins: int
    reached_by: float  # the share of the game's players who reach it


class CoinsIn(BaseModel):
    user_id: int
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(min_length=1, max_length=80)


class GrantIn(BaseModel):
    user_id: int
    item: str = Field(min_length=1, max_length=64)
