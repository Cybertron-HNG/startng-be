import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator

SLUG = r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"


def _https(value: str | None) -> str | None:
    """A link, or nothing. Only https:// is accepted, so nobody can slip in a javascript: address."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not re.match(r"^https://[^\s/$.?#][^\s]*$", value):
        raise ValueError("Use a full link that starts with https://")
    return value


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


class Highlight(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    text: str = Field(default="", max_length=160)


class Partner(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    url: str | None = Field(default=None, max_length=300)

    _link = field_validator("url")(_https)


class EventIn(BaseModel):
    slug: str | None = Field(default=None, pattern=SLUG)  # made from the title when left out
    title: str = Field(min_length=2, max_length=80)
    tagline: str | None = Field(default=None, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    starts_at: datetime
    ends_at: datetime | None = None
    venue: str = Field(min_length=2, max_length=120)
    address: str | None = Field(default=None, max_length=200)
    ticket_url: str | None = Field(default=None, max_length=300)
    ticket_label: str = Field(default="Get tickets", min_length=2, max_length=30)
    website_url: str | None = Field(default=None, max_length=300)
    highlights: list[Highlight] = Field(default_factory=list, max_length=6)
    partners: list[Partner] = Field(default_factory=list, max_length=12)

    _links = field_validator("ticket_url", "website_url")(_https)

    @model_validator(mode="after")
    def _dates(self):
        self.starts_at = _utc(self.starts_at)
        if self.ends_at is not None:
            self.ends_at = _utc(self.ends_at)
            if self.ends_at <= self.starts_at:
                raise ValueError("The event has to end after it starts")
        return self


class ImageOut(BaseModel):
    id: int
    path: str  # on the API, e.g. /events/tech-ember-fest-2026/images/3.webp
    alt: str | None


class EventOut(BaseModel):
    id: int
    slug: str
    title: str
    tagline: str | None
    description: str
    starts_at: datetime
    ends_at: datetime | None
    venue: str
    address: str | None
    ticket_url: str | None
    ticket_label: str
    website_url: str | None
    highlights: list[Highlight]
    partners: list[Partner]
    images: list[ImageOut]
    spotlight: bool
    phase: str  # upcoming, now, past


class SpotlightIn(BaseModel):
    on: bool
