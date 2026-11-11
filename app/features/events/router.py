from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import admin as admins
from ...db import get_db
from . import service
from .models import Event, EventImage
from .schemas import SLUG, EventIn, EventOut, ImageOut, SpotlightIn

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["events"])
manage = admins.require("events.manage")
SLUG_PATH = Path(pattern=SLUG)


def event_out(db: Session, event: Event) -> EventOut:
    pics = [ImageOut(id=i.id, path=f"/events/{event.slug}/images/{i.id}.webp", alt=i.alt) for i in service.images(db, event)]
    return EventOut(
        id=event.id, slug=event.slug, title=event.title, tagline=event.tagline, description=event.description,
        starts_at=service._aware(event.starts_at), ends_at=service._aware(event.ends_at) if event.ends_at else None,
        venue=event.venue, address=event.address, ticket_url=event.ticket_url, ticket_label=event.ticket_label,
        website_url=event.website_url, highlights=event.highlights or [], partners=event.partners or [], images=pics,
        spotlight=bool(event.spotlight), phase=service.phase(event))


def _get(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such event")
    return event


def _fail(err: service.EventError):
    raise HTTPException(err.status, err.message)


# ---- public

@router.get("/events/spotlight", response_model=EventOut | None)
def spotlight(db: Session = Depends(get_db)):
    """The event spotlighted on the homepage, or null. The homepage asks this first and hides the promotions while it answers."""
    event = service.spotlighted(db)
    return event_out(db, event) if event else None


@router.get("/events/{slug}", response_model=EventOut)
def event_page(slug: str = SLUG_PATH, db: Session = Depends(get_db)):
    event = service.by_slug(db, slug)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No event here")
    return event_out(db, event)


@router.get("/events/{slug}/images/{image_id}.webp", response_class=Response)
def event_image(image_id: int, slug: str = SLUG_PATH, db: Session = Depends(get_db)):
    event = service.by_slug(db, slug)
    row = db.get(EventImage, image_id) if event else None
    if row is None or row.event_id != event.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such picture")
    # An image never changes once saved (a new upload is a new id), so it can be kept for a year.
    return Response(row.data, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---- admin: the Events tab

@router.get("/admin/events", response_model=list[EventOut])
def admin_list(admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    return [event_out(db, e) for e in db.scalars(select(Event).order_by(Event.starts_at.desc()))]


@router.post("/admin/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create(body: EventIn, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    fields = body.model_dump(exclude={"slug"})
    event = Event(slug=service.unique_slug(db, body.slug or service.slugify(body.title)), **fields)
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That address is already used by another event")
    admins.log(db, admin, "events.create", event.slug)
    db.commit()
    return event_out(db, event)


@router.put("/admin/events/{event_id}", response_model=EventOut)
def change(event_id: int, body: EventIn, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    event = _get(db, event_id)
    for key, value in body.model_dump(exclude={"slug"}).items():
        setattr(event, key, value)
    if body.slug and body.slug != event.slug:
        event.slug = service.unique_slug(db, body.slug, skip_id=event.id)
    admins.log(db, admin, "events.change", event.slug)
    db.commit()
    return event_out(db, event)


@router.delete("/admin/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(event_id: int, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    event = _get(db, event_id)
    admins.log(db, admin, "events.delete", event.slug)
    db.query(EventImage).filter(EventImage.event_id == event.id).delete()
    db.delete(event)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/admin/events/{event_id}/spotlight", response_model=EventOut)
def spotlight_event(event_id: int, body: SpotlightIn, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Turn the homepage spotlight on for this event (any other event's goes off) or off."""
    event = _get(db, event_id)
    try:
        service.set_spotlight(db, event, body.on)
    except service.EventError as err:
        db.rollback()
        _fail(err)
    admins.log(db, admin, "events.spotlight_on" if body.on else "events.spotlight_off", event.slug)
    db.commit()
    return event_out(db, event)


async def _picture(request: Request) -> bytes:
    """The picture, sent as the raw request body, read with a size cap whatever Content-Length says."""
    too_big = HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The picture must be 5 MB or less")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > service.MAX_UPLOAD_BYTES:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > service.MAX_UPLOAD_BYTES:
            raise too_big
        chunks.append(chunk)
    if not size:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send a PNG, JPEG or WebP picture")
    return b"".join(chunks)


@router.post("/admin/events/{event_id}/images", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def add_image(event_id: int, alt: str | None = Query(default=None, max_length=160), admin: admins.AdminUser = Depends(manage),
              db: Session = Depends(get_db), data: bytes = Depends(_picture)):
    event = _get(db, event_id)
    try:
        service.add_image(db, event, data, alt)
    except service.EventError as err:
        db.rollback()
        _fail(err)
    admins.log(db, admin, "events.add_image", event.slug)
    db.commit()
    return event_out(db, event)


@router.put("/admin/events/{event_id}/images/{image_id}/cover", response_model=EventOut)
def make_cover(event_id: int, image_id: int, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    event = _get(db, event_id)
    try:
        service.make_cover(db, event, image_id)
    except service.EventError as err:
        db.rollback()
        _fail(err)
    db.commit()
    return event_out(db, event)


@router.delete("/admin/events/{event_id}/images/{image_id}", response_model=EventOut)
def delete_image(event_id: int, image_id: int, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    event = _get(db, event_id)
    row = db.get(EventImage, image_id)
    if row is None or row.event_id != event.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such picture")
    db.delete(row)
    db.flush()
    if event.spotlight and not service.images(db, event):
        event.spotlight = False  # a spotlighted event always has a picture; without one it steps aside
    admins.log(db, admin, "events.delete_image", event.slug)
    db.commit()
    return event_out(db, event)
