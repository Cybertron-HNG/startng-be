"""Challenge links (B1). A player turns one of their runs into a link, "Loki scored 4,200 on Relic Hunter. Can you beat
it?". The run comes from the run log (never from the request), so a faked score is impossible. A friend who accepts
the challenge has their runs for that game counted from then on; the best one is kept, and when it beats the
challenge the challenger is told (event challenge_beaten, and an email if they chose "records" emails).
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events, flags
from ... import leaderboard as boards
from ...config import settings
from ...db import SessionLocal
from ...features.email import service as email
from ...features.progress.models import Run
from ...models import Game, User
from ...routers.games import _player_name
from ...routers.pages import _title
from .models import Challenge, ChallengeAttempt

log = logging.getLogger(__name__)

NAME = "challenges"
LIVES_FOR = timedelta(days=30)
KEPT_AFTER = timedelta(days=30)  # an expired challenge is deleted this long after it ends
MAX_ACTIVE = 30  # live challenges per player
MAX_PER_HOUR = 20
RUNS_LOOKED_AT = 1000  # the player's latest runs in a game that "my best" is picked from


class ChallengeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def schema_of(game: Game) -> list[dict]:
    return game.leaderboard or boards.DEFAULT


def format_stat(stat: dict, value: int | None) -> str:
    if value is None:
        return "-"
    if stat["format"] == "time":
        return f"{value // 60}:{value % 60:02d}"
    return f"{value:,}"


def find(db: Session, code: str, at: datetime | None = None) -> Challenge | None:
    """A challenge that's still open (not expired) on a game that's still shown."""
    at = at or now()
    c = db.scalar(select(Challenge).where(Challenge.code == code))
    if c is None or _utc(c.expires_at) <= at:
        return None
    game = db.get(Game, c.game_name)
    return c if game is not None and game.hidden is None else None


def _best_run(db: Session, user_id: int, game: Game) -> Run | None:
    schema = schema_of(game)
    runs = db.scalars(select(Run).where(Run.user_id == user_id, Run.game_name == game.name, Run.flagged.is_(False))
                      .order_by(Run.id.desc()).limit(RUNS_LOOKED_AT)).all()
    return min(runs, key=lambda r: boards.rank_key(schema, r.stats), default=None)


def create(db: Session, user: User, game_name: str, run_id: int | None = None, at: datetime | None = None) -> Challenge:
    """A challenge for the player's run: the one named, or their best in the game."""
    at = at or now()
    game = db.get(Game, game_name)
    if game is None or game.hidden is not None:
        raise ChallengeError("no_game", "There's no game with that name", 404)
    if run_id is not None:
        run = db.get(Run, run_id)
        if run is None or run.user_id != user.id or run.game_name != game.name or run.flagged:
            raise ChallengeError("bad_run", "That run isn't yours, or it isn't for this game", 400)
    else:
        run = _best_run(db, user.id, game)
        if run is None:
            raise ChallengeError("no_run", "Play the game first: a challenge needs a score to beat", 400)
    # The same run again gives the same link.
    same = db.scalar(select(Challenge).where(Challenge.challenger_id == user.id, Challenge.run_id == run.id, Challenge.expires_at > at))
    if same is not None:
        return same
    if db.scalar(select(func.count()).select_from(Challenge).where(Challenge.challenger_id == user.id, Challenge.created_at > at - timedelta(hours=1))) >= MAX_PER_HOUR:
        raise ChallengeError("too_many", "That's a lot of challenges at once. Try again in a little while", 429)
    if db.scalar(select(func.count()).select_from(Challenge).where(Challenge.challenger_id == user.id, Challenge.expires_at > at)) >= MAX_ACTIVE:
        raise ChallengeError("too_many_live", f"You have {MAX_ACTIVE} challenges open. Delete one first", 429)
    db.execute(delete(Challenge).where(Challenge.expires_at < at - KEPT_AFTER))
    c = Challenge(code=secrets.token_urlsafe(8), challenger_id=user.id, game_name=game.name, run_id=run.id,
                  score=run.score, stats=run.stats, created_at=at, expires_at=at + LIVES_FOR)
    db.add(c)
    db.commit()
    return c


def accept(db: Session, c: Challenge, user: User, at: datetime | None = None) -> ChallengeAttempt:
    """The friend takes the challenge; runs they play from now on count. Accepting twice changes nothing."""
    if c.challenger_id == user.id:
        raise ChallengeError("own", "That's your own challenge. Send the link to a friend", 400)
    at = at or now()
    attempt = db.get(ChallengeAttempt, (c.id, user.id))
    if attempt is None:
        attempt = ChallengeAttempt(challenge_id=c.id, user_id=user.id, joined_at=at)
        db.add(attempt)
        try:
            db.commit()
        except IntegrityError:  # two taps at once
            db.rollback()
            attempt = db.get(ChallengeAttempt, (c.id, user.id))
    return attempt


def short_by(c: Challenge, attempt: ChallengeAttempt, game: Game) -> int | None:
    """How far the friend's best is from the challenge on the first stat (None: no run yet, or it's beaten)."""
    if attempt.best_stats is None or attempt.beaten_at is not None:
        return None
    first = schema_of(game)[0]
    mine, theirs = attempt.best_stats.get(first["key"]), c.stats.get(first["key"])
    if mine is None or theirs is None:
        return None
    return max(0, theirs - mine if first["order"] == "highest" else mine - theirs)


# ---- the friend's runs

def record(db: Session, user_id: int, run: Run, at: datetime | None = None) -> list[Challenge]:
    """Count a run for every challenge the player accepted on this game. Returns the challenges it beat."""
    game = db.get(Game, run.game_name)
    if game is None or run.flagged:
        return []
    schema = schema_of(game)
    at = at or now()
    rows = db.execute(select(ChallengeAttempt, Challenge).join(Challenge, Challenge.id == ChallengeAttempt.challenge_id).where(
        ChallengeAttempt.user_id == user_id, Challenge.game_name == run.game_name, Challenge.expires_at > at)).all()
    beaten = []
    for attempt, c in rows:
        if _utc(run.created_at) < _utc(attempt.joined_at):
            continue  # played before they took the challenge
        if attempt.best_stats is None or boards.is_better(schema, run.stats, attempt.best_stats):
            attempt.best_score, attempt.best_stats = run.score, run.stats
        if attempt.beaten_at is None and boards.is_better(schema, run.stats, c.stats):
            attempt.beaten_at = at
            beaten.append(c)
    db.commit()
    return beaten


def tell_challenger(db: Session, c: Challenge, friend: User) -> None:
    game = db.get(Game, c.game_name)
    stat = schema_of(game)[0]
    events.publish("challenge_beaten", {"challenge_id": c.id, "challenger_id": c.challenger_id, "user_id": friend.id})
    email.notify(c.challenger_id, "challenge_beaten", {
        "friend": _player_name(friend), "game": _title(c.game_name), "score": format_stat(stat, c.score),
        "link": settings.site_url.rstrip("/") + f"/c/{c.code}"}, db=db)
    db.commit()


@events.subscribe("run_recorded")
def on_run_recorded(payload: dict) -> None:
    if not flags.is_on(NAME):
        return
    with SessionLocal() as db:
        run = db.get(Run, payload["run_id"])
        friend = db.get(User, payload["user_id"])
        if run is None or friend is None:
            return
        for c in record(db, friend.id, run):
            tell_challenger(db, c, friend)


email.register_kind("challenge_beaten", "records", lambda d: email.Note(
    subject=f'{d["friend"]} beat your {d["game"]} score',
    html=f'<p style="font-size:18px;font-weight:700;margin:0 0 8px">{email.esc(d["friend"])} beat your score on {email.esc(d["game"])}</p>'
         f'<p>You set {email.esc(d["score"])}. Time for a rematch?</p>'
         f'<p><a href="{email.esc(d["link"])}" style="display:inline-block;background:#d9f99d;color:#1d1d1b;padding:10px 16px;'
         f'border-radius:10px;font-weight:700;text-decoration:none">See the challenge</a></p>',
    text=f'{d["friend"]} beat your score on {d["game"]}. You set {d["score"]}. Time for a rematch?\n\n{d["link"]}'))
