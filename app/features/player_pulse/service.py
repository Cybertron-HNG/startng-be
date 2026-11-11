"""What other features may call (this feature's contract)."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import PulseVote

WINDOW_DAYS = 7  # the creator's summary covers this many days
LATEST_NOTES = 5


def today() -> date:
    return datetime.now(timezone.utc).date()


def vote(db: Session, user_id: int, game_name: str, feel: str, note: str | None) -> PulseVote:
    """Record a player's answer. A second answer on the same day replaces the first."""
    day = today()
    row = db.scalar(select(PulseVote).where(PulseVote.user_id == user_id, PulseVote.game_name == game_name, PulseVote.day == day))
    if row is None:
        row = PulseVote(user_id=user_id, game_name=game_name, day=day)
        db.add(row)
    row.feel, row.note, row.created_at = feel, (note or "").strip() or None, datetime.now(timezone.utc)
    return row


def summary(db: Session, game_name: str, days: int = WINDOW_DAYS) -> dict:
    """Counts per feel over the last `days` days, and the latest notes. Aggregates only: never who answered."""
    since = today() - timedelta(days=days - 1)
    where = (PulseVote.game_name == game_name, PulseVote.day >= since)
    counts = dict(db.execute(select(PulseVote.feel, func.count()).where(*where).group_by(PulseVote.feel)).all())
    answers = sum(counts.values())
    notes = db.scalars(select(PulseVote).where(*where, PulseVote.note.is_not(None))
                       .order_by(PulseVote.created_at.desc()).limit(LATEST_NOTES)).all()
    return {
        "game": game_name, "days": days, "answers": answers,
        "loved": counts.get("loved", 0), "too_hard": counts.get("too_hard", 0), "too_easy": counts.get("too_easy", 0),
        "loved_pct": round(100 * counts.get("loved", 0) / answers) if answers else 0,
        "notes": [{"feel": n.feel, "note": n.note, "at": n.created_at} for n in notes],
    }
