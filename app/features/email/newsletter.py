"""The newsletter (H1). An admin writes one, sends themselves a test, then sends it to everyone on the newsletter topic.

The body is plain text: a blank line starts a new paragraph, a line starting with "# " is a heading, and
[words](https://link) is a link. Nothing else is read as markup, so what an admin types can't break the layout.
Newsletters go out at most every NEWSLETTER_EVERY (the topic promises "at most every 2 weeks"): sending sooner needs
the admin to say so (force), and the composer warns first.
"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import admin as adm
from ... import flags
from ...admin import AdminUser, require
from ...config import settings
from ...db import get_db
from . import service
from .models import Newsletter, Subscriber, SubscriberTopic
from .service import Note, esc

NEWSLETTER_EVERY = timedelta(days=14)
LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,500})\)")

PERMISSIONS = {"newsletter.send": "Write and send the start.ng newsletter"}


def _inline(line: str) -> tuple[str, str]:
    """One line as (html, text): its [words](link)s become links, everything else is escaped."""
    html_out, text_out, at = [], [], 0
    for m in LINK.finditer(line):
        html_out.append(esc(line[at:m.start()]))
        html_out.append(f'<a href="{esc(m.group(2))}" style="color:#4d7c0f">{esc(m.group(1))}</a>')
        text_out.append(line[at:m.start()] + f"{m.group(1)} ({m.group(2)})")
        at = m.end()
    html_out.append(esc(line[at:]))
    text_out.append(line[at:])
    return "".join(html_out), "".join(text_out)


def render_body(body: str) -> tuple[str, str]:
    """The newsletter text as an email fragment and as plain text."""
    html_parts, text_parts = [], []
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n").strip()):
        block = block.strip()
        if not block:
            continue
        if block.startswith("# "):
            h, t = _inline(block[2:].strip().replace("\n", " "))
            html_parts.append(f'<p style="font-size:18px;font-weight:700;margin:0 0 8px">{h}</p>')
            text_parts.append(t.upper())
            continue
        lines = [_inline(line) for line in block.split("\n")]
        html_parts.append("<p>" + "<br>".join(h for h, _ in lines) + "</p>")
        text_parts.append("\n".join(t for _, t in lines))
    return "".join(html_parts), "\n\n".join(text_parts)


def _render(d: dict) -> Note:
    html_body, text_body = render_body(d["body"])
    return Note(subject=d["subject"], html=html_body, text=text_body)


def _render_test(d: dict) -> Note:
    note = _render(d)
    return Note(subject=f"[Test] {note.subject}", html=note.html, text=note.text)


service.register_kind("newsletter", "newsletter", _render)
service.register_kind("newsletter_test", None, _render_test)


# ---- sending

def audience(db: Session) -> int:
    """How many addresses a newsletter would go to."""
    return db.scalar(select(func.count(func.distinct(Subscriber.id))).join(SubscriberTopic).where(
        SubscriberTopic.topic == "newsletter", Subscriber.confirmed_at.is_not(None), Subscriber.bad_at.is_(None))) or 0


def last_sent(db: Session) -> Newsletter | None:
    return db.scalar(select(Newsletter).where(Newsletter.sent_at.is_not(None)).order_by(Newsletter.sent_at.desc()).limit(1))


def too_soon(db: Session) -> Newsletter | None:
    """The newsletter that was sent less than NEWSLETTER_EVERY ago, if any."""
    latest = last_sent(db)
    return latest if latest is not None and service.now() - service._utc(latest.sent_at) < NEWSLETTER_EVERY else None


def send_test(db: Session, letter: Newsletter, to: str) -> None:
    """The newsletter to one address (the admin's own), straight away, outside the topics and the daily cap."""
    sub = service._subscriber(db, to)
    if sub is None:
        sub = Subscriber(email=to.lower(), unsubscribe_token=service._new_token(), confirmed_at=service.now())
        db.add(sub)
        db.flush()
    service._queue(db, sub, "newsletter_test", {"subject": letter.subject, "body": letter.body})
    db.commit()
    service.send_due(db)


def _tomorrow_morning():
    """9 AM tomorrow in start.ng's time zone: when a newsletter that hit today's cap goes out instead."""
    zone = timezone(timedelta(hours=settings.tournament_utc_offset))
    local = service.now().astimezone(zone)
    return (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def send(db: Session, letter: Newsletter, by: str) -> int:
    """Queue the newsletter for everyone on the topic, once. Returns how many.

    A subscriber who already had today's email would normally miss a note (the 1-a-day cap), but a newsletter is
    sent once and must not be lost, so theirs goes out tomorrow morning instead."""
    if not flags.is_on(service.NAME):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is switched off")
    subs = db.scalars(select(Subscriber).join(SubscriberTopic).where(
        SubscriberTopic.topic == "newsletter", Subscriber.confirmed_at.is_not(None), Subscriber.bad_at.is_(None))).all()
    data = {"subject": letter.subject, "body": letter.body}
    later = _tomorrow_morning()
    for sub in subs:
        row = service._queue(db, sub, "newsletter", data)
        if row.status == "capped":
            row.status, row.day, row.send_after = "queued", later.date().isoformat(), later
    letter.sent_at, letter.sent_by, letter.recipients = service.now(), by, len(subs)
    db.commit()
    return len(subs)


# ---- the admin's API

router = APIRouter(tags=["email"])
send_it = require("newsletter.send")


class LetterIn(BaseModel):
    subject: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=20000)


class SendIn(BaseModel):
    force: bool = False  # send although the last one went out less than two weeks ago


class LetterOut(BaseModel):
    id: int
    subject: str
    body: str
    created_by: str
    updated_at: datetime
    sent_at: datetime | None
    sent_by: str | None
    recipients: int | None
    preview_html: str


class Newsletters(BaseModel):
    audience: int  # who a send would reach
    every_days: int
    last_sent_at: datetime | None  # when the last newsletter went out
    letters: list[LetterOut]


def _out(n: Newsletter) -> LetterOut:
    return LetterOut(id=n.id, subject=n.subject, body=n.body, created_by=n.created_by, updated_at=n.updated_at,
                     sent_at=n.sent_at, sent_by=n.sent_by, recipients=n.recipients, preview_html=render_body(n.body)[0])


def _draft(db: Session, letter_id: int) -> Newsletter:
    n = db.get(Newsletter, letter_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Newsletter not found")
    return n


def _editable(db: Session, letter_id: int) -> Newsletter:
    n = _draft(db, letter_id)
    if n.sent_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That newsletter was already sent")
    return n


@router.get("/admin/newsletters", response_model=Newsletters)
def list_newsletters(admin: AdminUser = Depends(send_it), db: Session = Depends(get_db)):
    latest = last_sent(db)
    letters = db.scalars(select(Newsletter).order_by(Newsletter.id.desc()).limit(50)).all()
    return Newsletters(audience=audience(db), every_days=NEWSLETTER_EVERY.days,
                       last_sent_at=latest.sent_at if latest else None, letters=[_out(n) for n in letters])


@router.post("/admin/newsletters", response_model=LetterOut, status_code=status.HTTP_201_CREATED)
def create_newsletter(body: LetterIn, admin: AdminUser = Depends(send_it), db: Session = Depends(get_db)):
    n = Newsletter(subject=body.subject.strip(), body=body.body, created_by=admin.email)
    db.add(n)
    adm.log(db, admin, "newsletter.create", body.subject.strip()[:60])
    db.commit()
    return _out(n)


@router.put("/admin/newsletters/{letter_id}", response_model=LetterOut)
def edit_newsletter(letter_id: int, body: LetterIn, admin: AdminUser = Depends(send_it), db: Session = Depends(get_db)):
    n = _editable(db, letter_id)
    n.subject, n.body = body.subject.strip(), body.body
    db.commit()
    return _out(n)


@router.delete("/admin/newsletters/{letter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_newsletter(letter_id: int, admin: AdminUser = Depends(send_it), db: Session = Depends(get_db)):
    db.delete(_editable(db, letter_id))
    adm.log(db, admin, "newsletter.delete", str(letter_id))
    db.commit()


@router.post("/admin/newsletters/{letter_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_newsletter(letter_id: int, admin: AdminUser = Depends(send_it), db: Session = Depends(get_db)):
    """Send the newsletter to the admin's own address."""
    send_test(db, _draft(db, letter_id), admin.email)
    return {"to": admin.email}


@router.post("/admin/newsletters/{letter_id}/send", response_model=LetterOut)
def send_newsletter(letter_id: int, body: SendIn, admin: AdminUser = Depends(send_it), db: Session = Depends(get_db)):
    n = _editable(db, letter_id)
    recent = too_soon(db)
    if recent is not None and not body.force:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f'"{recent.subject}" went out on {service._utc(recent.sent_at):%d %b}. Newsletters go out at '
                            f"most every {NEWSLETTER_EVERY.days} days. Send anyway to go ahead.")
    count = send(db, n, admin.email)
    adm.log(db, admin, "newsletter.send", n.subject[:60], recipients=count)
    db.commit()
    return _out(n)
