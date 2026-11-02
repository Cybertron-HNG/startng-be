"""Challenge links (PRD B1): a player turns one of their runs into a link, start.ng/c/<code>; friends who take it on have
their next runs on that game compared with it, and the challenger hears when it's beaten.

The score always comes from the run log (contract C1), never from a request, and only runs the Progress Engine
records count: runs held for review (score integrity) never publish run_recorded, so they can't beat anything until an
admin clears them."""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ... import events
from ...activity import record as log_activity
from ...db import SessionLocal
from ...models import Game, User
from ...routers.games import _player_name
from ..email import service as email
from ..progress.models import Run
from .models import Challenge, ChallengeAttempt

log = logging.getLogger(__name__)

LASTS = timedelta(days=30)  # a challenge link works this long
PER_DAY = 20  # challenges one player can make in 24 hours


class Refused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when  # SQLite drops the zone


# ---- the game's first stat: what's compared, and which way is better


def first_stat(game: Game) -> dict:
    """The column a challenge is about: the game's first leaderboard stat, or the plain score."""
    board = game.leaderboard or []
    return board[0] if board else {"key": "score", "label": "Score", "order": "highest", "format": "number"}


def beats(game: Game, score: int, target: int) -> bool:
    return score < target if first_stat(game).get("order") == "lowest" else score > target


def better(game: Game, a: int | None, b: int) -> int:
    return b if a is None or beats(game, b, a) else a


def format_score(game: Game, value: int) -> str:
    """As the website's boards show it: 4,200 or 3:05 for a time in seconds."""
    if first_stat(game).get("format") == "time":
        return f"{value // 60}:{value % 60:02d}"
    return f"{value:,}"


def short_by(game: Game, best: int | None, target: int) -> int | None:
    """How far a best is from the target (always positive), or None if it's beaten or there's no run yet."""
    if best is None or beats(game, best, target):
        return None
    return abs(target - best)


# ---- making, finding, taking on and deleting


def create(db: Session, user: User, run_id: int) -> Challenge:
    """A challenge from one of the player's own runs. Asking again for the same run gives back the same open link."""
    run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise Refused(404, "No run of yours with that id")
    if run.flagged:
        raise Refused(409, "This run is being checked, so it can't be a challenge yet")
    open_ = db.scalar(select(Challenge).where(Challenge.run_id == run.id, Challenge.expires_at > now()))
    if open_ is not None:
        return open_
    made = db.scalar(select(func.count()).select_from(Challenge)
                     .where(Challenge.challenger_id == user.id, Challenge.created_at > now() - timedelta(days=1))) or 0
    if made >= PER_DAY:
        raise Refused(429, f"You can make {PER_DAY} challenges a day. Try again tomorrow")
    c = Challenge(code=secrets.token_urlsafe(8), challenger_id=user.id, game_name=run.game_name, run_id=run.id,
                  score=run.score, stats=run.stats, created_at=now(), expires_at=now() + LASTS)
    db.add(c)
    log_activity(db, user, "player", "challenge.created", c.code)
    db.commit()
    return c


def best_run(db: Session, user: User, game_name: str) -> int:
    """The id of the player's best unflagged run on a game still in the run log (for "Challenge a friend" on its page)."""
    game = db.get(Game, game_name)
    if game is None:
        raise Refused(404, "No game with that name")
    order = Run.score.asc() if first_stat(game).get("order") == "lowest" else Run.score.desc()
    run_id = db.scalar(select(Run.id).where(Run.user_id == user.id, Run.game_name == game_name, Run.flagged.is_(False))
                       .order_by(order, Run.id).limit(1))
    if run_id is None:
        raise Refused(404, "Play it first: a challenge is one of your runs")
    return run_id


def has_run(db: Session, user: User, game_name: str) -> bool:
    """Whether the player has a run on a game that "Challenge a friend" can use, so its page only shows the button then."""
    try:
        best_run(db, user, game_name)
    except Refused:
        return False
    return True


def find(db: Session, code: str) -> Challenge | None:
    """An open challenge (None once it has expired, or if the code doesn't exist)."""
    c = db.scalar(select(Challenge).options(selectinload(Challenge.challenger), selectinload(Challenge.game))
                  .where(Challenge.code == code))
    return c if c and _utc(c.expires_at) > now() and not c.game.hidden else None


def attempt_of(db: Session, c: Challenge, user_id: int) -> ChallengeAttempt | None:
    return db.scalar(select(ChallengeAttempt).where(ChallengeAttempt.challenge_id == c.id, ChallengeAttempt.user_id == user_id))


def attempts(db: Session, c: Challenge) -> list[ChallengeAttempt]:
    """Everyone who took it on, best first (those with no run yet last)."""
    rows = list(db.scalars(select(ChallengeAttempt).options(selectinload(ChallengeAttempt.user))
                           .where(ChallengeAttempt.challenge_id == c.id)))
    lowest = first_stat(c.game).get("order") == "lowest"
    return sorted(rows, key=lambda a: (a.best is None, (a.best or 0) if lowest else -(a.best or 0), a.accepted_at))


def accept(db: Session, c: Challenge, user: User) -> ChallengeAttempt:
    """Take a challenge on (the Play button on its page). Runs from now on count. Taking it on twice is fine."""
    if c.challenger_id == user.id:
        raise Refused(409, "That's your own challenge. Send the link to a friend")
    a = attempt_of(db, c, user.id)
    if a is None:
        a = ChallengeAttempt(challenge_id=c.id, user_id=user.id, accepted_at=now())
        db.add(a)
        log_activity(db, user, "player", "challenge.accepted", c.code)
        db.commit()
    return a


def delete(db: Session, c: Challenge, user: User) -> None:
    if c.challenger_id != user.id:
        raise Refused(403, "Only the player who made it can delete it")
    db.delete(c)
    log_activity(db, user, "player", "challenge.deleted", c.code)
    db.commit()


# ---- counting runs


def count_run(db: Session, run: Run) -> list[ChallengeAttempt]:
    """Compare a recorded run with every open challenge its player took on for that game. Returns the attempts that
    beat their challenge with this run (each one only the first time)."""
    if run.flagged:
        return []
    at = _utc(run.created_at)
    rows = db.scalars(select(ChallengeAttempt).join(Challenge)
                      .options(selectinload(ChallengeAttempt.challenge).selectinload(Challenge.game))
                      .where(ChallengeAttempt.user_id == run.user_id, Challenge.game_name == run.game_name,
                             Challenge.expires_at > now()))
    newly_beaten = []
    for a in rows:
        if _utc(a.accepted_at) > at:
            continue  # played before taking the challenge on
        game = a.challenge.game
        a.best = better(game, a.best, run.score)
        if a.beaten_at is None and beats(game, run.score, a.challenge.score):
            a.beaten_at = now()
            newly_beaten.append(a)
    db.commit()
    return newly_beaten


@events.subscribe("run_recorded")
def on_run_recorded(payload: dict) -> None:
    from . import NAME  # here, not at the top: __init__ imports this module
    from ... import flags
    if not flags.is_on(NAME):
        return
    with SessionLocal() as db:
        run = db.get(Run, payload["run_id"])
        if run is None:
            return
        for a in count_run(db, run):
            c = a.challenge
            events.publish("challenge_beaten", {"challenge_id": c.id, "challenger_id": c.challenger_id, "user_id": a.user_id})
            try:
                email.notify(c.challenger_id, "challenge_beaten", {
                    "friend": _player_name(a.user), "game": c.game_name, "code": c.code,
                    "theirs": format_score(c.game, run.score), "yours": format_score(c.game, c.score),
                })
            except Exception:
                log.exception("Couldn't queue the challenge-beaten email for challenge %s", c.id)


# ---- the email (H1 sends it; the "records" topic: someone beat your score)


def _render_beaten(d: dict) -> email.Note:
    from ...routers.pages import _title
    game, link = _title(d["game"]), email._link(f"/c/{d['code']}")
    subject = f"{d['friend']} beat your challenge on {game}"
    text = f"{d['friend']} scored {d['theirs']} on {game} and beat your {d['yours']}. Win it back: {link}"
    html = (f"<p><b>{email.esc(d['friend'])}</b> scored <b>{email.esc(d['theirs'])}</b> on {email.esc(game)} and beat "
            f"your {email.esc(d['yours'])}.</p><p><a href=\"{email.esc(link)}\">See the challenge and win it back</a></p>")
    return email.Note(subject=subject, html=html, text=text)


email.register_kind("challenge_beaten", "records", _render_beaten)
