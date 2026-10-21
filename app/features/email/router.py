"""Email's API (H1). Players: /email/me. Visitors: /email/subscribe, /email/confirm, /email/unsubscribe (the links in
every email). Brevo: /email/webhooks/brevo. Admins: /admin/email. 404 while the flag is off."""
import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...config import settings
from ...db import get_db
from ...models import User
from ...security import current_user
from . import service
from .models import Email

router = APIRouter(tags=["email"])


class Topic(BaseModel):
    key: str
    label: str
    on: bool


class MyEmail(BaseModel):
    email: str
    topics: list[Topic]
    bad: bool  # mail to this address bounced, so we've stopped writing to it


class TopicsIn(BaseModel):
    topics: list[str]


class SubscribeIn(BaseModel):
    email: EmailStr
    topics: list[str] = ["tournament"]


class TokenIn(BaseModel):
    token: str
    topic: str | None = None


class Done(BaseModel):
    email: str
    topics: list[str]


def _mine(user: User, sub) -> MyEmail:
    on = set(sub.topic_names) if sub else set()
    return MyEmail(email=user.email, bad=bool(sub and sub.bad_at),
                   topics=[Topic(key=k, label=v, on=k in on) for k, v in service.TOPICS.items()])


def _masked(email: str) -> str:
    """ada@example.com -> a**@example.com: enough for the person to recognise, not enough to collect."""
    name, _, domain = email.partition("@")
    return f"{name[:1]}{'*' * max(2, len(name) - 1)}@{domain}"


@router.get("/email/me", response_model=MyEmail)
def my_email(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _mine(user, service.subscriber_for(db, user))


@router.put("/email/me", response_model=MyEmail)
def set_my_email(body: TopicsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        sub = service.set_my_topics(db, user, body.topics)
    except KeyError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, e.args[0])
    return _mine(user, sub)


@router.post("/email/subscribe", status_code=status.HTTP_202_ACCEPTED)
def subscribe(body: SubscribeIn, db: Session = Depends(get_db)):
    """A visitor leaves their address (the tournament page). We email a link; nothing is sent until they click it."""
    if not body.topics:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick at least one kind of email")
    try:
        service.ask_to_subscribe(db, body.email, body.topics)
    except KeyError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, e.args[0])
    return {"detail": "Check your inbox for a link to confirm."}


@router.post("/email/confirm", response_model=Done)
def confirm(body: TokenIn, db: Session = Depends(get_db)):
    sub = service.confirm(db, body.token)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That link has expired or was already used. Sign up again for a new one.")
    return Done(email=_masked(sub.email), topics=sub.topic_names)


@router.post("/email/unsubscribe", response_model=Done)
async def unsubscribe(request: Request, token: str | None = Query(None), topic: str | None = Query(None),
                      db: Session = Depends(get_db)):
    """One click. From the page an email links to (JSON body), or straight from the mail app: RFC 8058 one-click
    unsubscribe POSTs "List-Unsubscribe=One-Click" to the address in the List-Unsubscribe header (?token=)."""
    if request.headers.get("content-type", "").startswith("application/json"):
        body = TokenIn(**await request.json())
        token, topic = body.token, body.topic
    if not token:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Missing token")
    if topic is not None and topic not in service.TOPICS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f'Unknown email topic "{topic}"')
    sub = service.unsubscribe(db, token, topic)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We don't know that link")
    return Done(email=_masked(sub.email), topics=sub.topic_names)


# Brevo's transactional webhook events that mean "never write to this address again".
BAD_EVENTS = {"hard_bounce", "spam", "blocked", "invalid_email", "complaint"}


@router.post("/email/webhooks/brevo", status_code=status.HTTP_204_NO_CONTENT)
async def brevo_webhook(request: Request, key: str = Query(""), db: Session = Depends(get_db)):
    """Brevo tells us about bounces and spam complaints. Set its webhook URL to
    <api>/email/webhooks/brevo?key=<BREVO_WEBHOOK_KEY>. Brevo may send one event or a list."""
    if not settings.brevo_webhook_key or not hmac.compare_digest(key, settings.brevo_webhook_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    payload = await request.json()
    for event in payload if isinstance(payload, list) else [payload]:
        name, address = str(event.get("event", "")), event.get("email")
        if not address:
            continue
        if name in BAD_EVENTS:
            service.mark_bad(db, address, name)
            msg_id = event.get("message-id")
            if msg_id:
                row = db.scalar(select(Email).where(Email.provider_id == msg_id))
                if row is not None:
                    row.status, row.last_error = "bounced", name
                    db.commit()
        elif name == "unsubscribed":
            sub = service._subscriber(db, address)
            if sub is not None:
                service.unsubscribe(db, sub.unsubscribe_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- admin

view = require("email.view")


class OutboxRow(BaseModel):
    id: int
    to: str
    kinds: list[str]
    subject: str | None
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


class Outbox(BaseModel):
    provider: str
    only_to: list[str]
    counts: dict[str, int]  # by status, last 7 days
    subscribers: int  # confirmed addresses with at least one topic
    by_topic: dict[str, int]
    rows: list[OutboxRow]


@router.get("/admin/email", response_model=Outbox)
def outbox(status_: str | None = Query(None, alias="status"), admin: AdminUser = Depends(view), db: Session = Depends(get_db)):
    """The latest 100 emails (optionally one status), and how the list is doing."""
    from datetime import timedelta

    from .models import Subscriber, SubscriberTopic

    since = service.now() - timedelta(days=7)
    q = select(Email).order_by(Email.id.desc()).limit(100)
    if status_:
        q = q.where(Email.status == status_)
    counts = dict(db.execute(select(Email.status, func.count()).where(Email.created_at >= since).group_by(Email.status)).all())
    live = (Subscriber.confirmed_at.is_not(None), Subscriber.bad_at.is_(None))
    by_topic = dict(db.execute(select(SubscriberTopic.topic, func.count()).join(Subscriber).where(*live)
                               .group_by(SubscriberTopic.topic)).all())
    return Outbox(
        provider=settings.email_provider, only_to=sorted(service._only_to()), counts=counts, by_topic=by_topic,
        subscribers=db.scalar(select(func.count(func.distinct(Subscriber.id))).join(SubscriberTopic).where(*live)) or 0,
        rows=[OutboxRow(id=r.id, to=r.to_email, kinds=[n["kind"] for n in r.notes], subject=r.subject, status=r.status,
                        attempts=r.attempts, last_error=r.last_error, created_at=r.created_at, sent_at=r.sent_at)
              for r in db.scalars(q).all()],
    )


@router.post("/admin/email/test", response_model=OutboxRow)
def test_email(admin: AdminUser = Depends(view), db: Session = Depends(get_db)):
    """Send a test email to the admin's own address, straight away."""
    row = service.send_test(db, admin.email, admin.email)
    service.send_due(db)
    db.refresh(row)
    adm.log(db, admin, "email.test", admin.email, status=row.status)
    db.commit()
    return OutboxRow(id=row.id, to=row.to_email, kinds=["test"], subject=row.subject, status=row.status,
                     attempts=row.attempts, last_error=row.last_error, created_at=row.created_at, sent_at=row.sent_at)
