"""When people last used start.ng (users.last_seen_at), and the activity log (record)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import Activity, User

# Writing on every request would be wasteful: once every few minutes is plenty to rank people.
SEEN_EVERY = timedelta(minutes=10)


def mark_seen(db: Session, user: User, force: bool = False) -> None:
    now = datetime.now(timezone.utc)
    last = user.last_seen_at
    if last is not None and last.tzinfo is None:  # SQLite drops the zone
        last = last.replace(tzinfo=timezone.utc)
    if force or last is None or now - last >= SEEN_EVERY:
        user.last_seen_at = now
        db.commit()


def record(db: Session, actor: "User | str | None", kind: str, action: str, target: str | None = None, **detail) -> None:
    """Add a line to the activity log the admin page shows. The caller commits (so a rolled-back change leaves no line).
    actor: the user or their email; None for start.ng itself. kind: "player", "developer", "admin" or "system"."""
    email = actor.email if isinstance(actor, User) else actor
    db.add(Activity(actor=email, kind=kind, action=action, target=target, detail=detail or None))
