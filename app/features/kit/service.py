"""What other features may call (contract C11): session counts and lengths per game, and the 30-day clean-up."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...tournaments import utc
from .models import PlaySession

KEEP_DAYS = 30  # sessions are deleted after this (PRD M1)
HEARTBEAT_SECONDS = 30  # how often kit.js pings while the game is on screen
MIN_WRITE_SECONDS = 20  # a ping sooner than this after the last one isn't written (saves database writes)
MAX_SESSION_HOURS = 4  # longer sessions (a tab left open) count as this long in the averages


@dataclass
class SessionStats:
    sessions: int
    average_seconds: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(db: Session, game: str, session_id: str, event: str, user_id: int | None, kit: str | None) -> None:
    """Saves one ping from kit.js. A heartbeat for a session we never saw start (its start ping was lost) starts it;
    an end for an unknown session is ignored."""
    now = _now()
    row = db.scalar(select(PlaySession).where(PlaySession.session_id == session_id))
    if row is not None and row.game_name != game:
        return  # a session id belongs to one game; ignore another game reusing it
    if row is None:
        if event == "end":
            return
        db.add(PlaySession(session_id=session_id, game_name=game, user_id=user_id, kit_version=kit, started_at=now, last_seen_at=now))
        prune(db, now)
    elif event == "end":
        if row.ended_at is None:
            row.ended_at = now
            row.last_seen_at = now
    elif (now - utc(row.last_seen_at)).total_seconds() >= MIN_WRITE_SECONDS:
        row.last_seen_at = now
        if user_id and row.user_id is None:
            row.user_id = user_id  # they signed in during the session
    else:
        return
    db.commit()


def length_seconds(row: PlaySession) -> int:
    """How long a session lasted: from its start to its last ping, capped at MAX_SESSION_HOURS."""
    seconds = (utc(row.last_seen_at) - utc(row.started_at)).total_seconds()
    return int(min(max(seconds, 0), MAX_SESSION_HOURS * 3600))


def stats(db: Session, game: str, since: datetime | None = None) -> SessionStats:
    """Sessions of one game since a time (default: the last KEEP_DAYS days) and their average length."""
    since = since or _now() - timedelta(days=KEEP_DAYS)
    rows = db.scalars(select(PlaySession).where(PlaySession.game_name == game, PlaySession.started_at >= since)).all()
    if not rows:
        return SessionStats(sessions=0, average_seconds=0)
    return SessionStats(sessions=len(rows), average_seconds=round(sum(length_seconds(r) for r in rows) / len(rows)))


def prune(db: Session, now: datetime | None = None) -> None:
    """Deletes sessions older than KEEP_DAYS. Runs when a new session starts, so no scheduler is needed."""
    cutoff = (now or _now()) - timedelta(days=KEEP_DAYS)
    db.execute(delete(PlaySession).where(PlaySession.started_at < cutoff))


def playing_now(db: Session, within: timedelta = timedelta(seconds=90), now: datetime | None = None) -> dict[str, int]:
    """Players in each game right now: open sessions whose heartbeat (every 30 s while the game is on screen) came within
    `within`. Guests count too. {game: players}, games with nobody left out. Used by "playing now" (live, Ziki #37)."""
    since = (now or _now()) - within
    rows = db.execute(select(PlaySession.game_name, func.count()).where(
        PlaySession.last_seen_at >= since, PlaySession.ended_at.is_(None)).group_by(PlaySession.game_name)).all()
    return {game: n for game, n in rows}
