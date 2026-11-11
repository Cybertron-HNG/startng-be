"""What the events feature does: the one spotlighted event, turning it on and off, and pictures."""
import io
import re
import warnings
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ... import covers
from .models import Event, EventImage

AFTER_END = timedelta(days=1)  # a spotlighted event steps aside by itself this long after it ends
MAX_IMAGES = 8
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_WIDTH = 1600  # pictures are kept at most this wide, so the page stays quick


class EventError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when  # SQLite drops the zone


def _end(event: Event) -> datetime:
    """When it is over: its end time, or a day after it starts when it has none."""
    return _aware(event.ends_at) if event.ends_at else _aware(event.starts_at) + timedelta(days=1)


def phase(event: Event, at: datetime | None = None) -> str:
    """upcoming, now (started and not over) or past."""
    at = at or now()
    return "upcoming" if at < _aware(event.starts_at) else "now" if at < _end(event) else "past"


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:38].strip("-")
    return slug if len(slug) >= 3 else "event"


def unique_slug(db: Session, wanted: str, skip_id: int | None = None) -> str:
    slug, n = wanted, 2
    while (other := db.scalar(select(Event).where(Event.slug == slug))) is not None and other.id != skip_id:
        suffix = f"-{n}"
        slug, n = wanted[: 40 - len(suffix)].strip("-") + suffix, n + 1
    return slug


def by_slug(db: Session, slug: str) -> Event | None:
    return db.scalar(select(Event).where(Event.slug == slug))


def images(db: Session, event: Event) -> list[EventImage]:
    return list(db.scalars(select(EventImage).where(EventImage.event_id == event.id).order_by(EventImage.position, EventImage.id)))


def spotlighted(db: Session, at: datetime | None = None) -> Event | None:
    """The event on the homepage now, or None. It steps aside AFTER_END after it ends, so a forgotten one can't stay up."""
    at = at or now()
    event = db.scalar(select(Event).where(Event.spotlight.is_(True)))
    if event is None or at > _end(event) + AFTER_END:
        return None
    return event


def set_spotlight(db: Session, event: Event, on: bool) -> None:
    """Turn the spotlight on (every other event's goes off in the same step) or off. The caller commits."""
    if not on:
        event.spotlight = False
        return
    if not images(db, event):
        raise EventError("Add at least one picture before spotlighting an event")
    if now() > _end(event) + AFTER_END:
        raise EventError("This event is over, so it can't be spotlighted. Change its dates first.")
    db.execute(update(Event).where(Event.id != event.id, Event.spotlight.is_(True)).values(spotlight=False))
    db.flush()
    event.spotlight = True


def make_image(data: bytes) -> bytes:
    """A WebP, at most MAX_WIDTH wide, from an uploaded PNG, JPEG or WebP. Saved again, so nothing hidden in the file stays."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as img:
                if img.width * img.height > covers.MAX_PIXELS:
                    raise EventError("That picture is too big, use one under 8000 x 5000")
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info else "RGB")
                if img.width > MAX_WIDTH:
                    img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                img.save(out, "WEBP", quality=82)
                return out.getvalue()
    except EventError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, SyntaxError) as err:
        raise EventError("Use a PNG, JPEG or WebP picture") from err


def add_image(db: Session, event: Event, data: bytes, alt: str | None) -> EventImage:
    have = images(db, event)
    if len(have) >= MAX_IMAGES:
        raise EventError(f"An event can have {MAX_IMAGES} pictures at most")
    row = EventImage(event_id=event.id, position=(have[-1].position + 1) if have else 0, alt=(alt[:160] if alt else None), data=make_image(data))
    db.add(row)
    db.flush()
    return row


def make_cover(db: Session, event: Event, image_id: int) -> None:
    """Put this picture first. The others keep their order."""
    every = images(db, event)
    chosen = next((i for i in every if i.id == image_id), None)
    if chosen is None:
        raise EventError("No such picture", 404)
    for pos, row in enumerate([chosen, *[i for i in every if i.id != image_id]]):
        row.position = pos
