"""When people last used start.ng (users.last_seen_at). The Hangout seats the most recent ones."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import User

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
