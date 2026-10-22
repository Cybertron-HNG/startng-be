"""Player profiles (S1): what a player's page shows, and who may see it. Only the name and character are public, never the
email. A page is public by default (the player can switch it off); a private page answers 404 to everyone but its owner,
and the Progress Engine's public endpoint follows the same choice (progress.register_visibility)."""
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...features.integrity.service import rank_of
from ...features.progress import service as progress
from ...features.progress.models import Run
from ...leaderboard import DEFAULT as DEFAULT_BOARD
from ...leaderboard import stats_of
from ...models import Favorite, Game, Score, TournamentPoints, User
from ...routers.games import _player_name
from ...routers.pages import _title, slugify
from . import badges
from .models import ProfileSettings

NAME = "profiles"
BEST_GAMES = 12  # games shown with a best score and rank
MOST_PLAYED = 5
FAVORITES = 12


def settings_of(db: Session, user_id: int) -> ProfileSettings:
    """The player's settings (the defaults when they never changed anything; not saved until they do)."""
    row = db.get(ProfileSettings, user_id)
    if row is not None:
        return row
    return ProfileSettings(user_id=user_id, public=True, show_favorites=True, show_ranks=True, notice_seen=False)


def update_settings(db: Session, user_id: int, **changes) -> ProfileSettings:
    row = db.get(ProfileSettings, user_id)
    if row is None:
        row = ProfileSettings(user_id=user_id, public=True, show_favorites=True, show_ranks=True, notice_seen=False)
        db.add(row)
    for key, value in changes.items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    return row


def is_public(db: Session, user_id: int) -> bool:
    return settings_of(db, user_id).public


progress.register_visibility("profiles", is_public)


def slug_for(user: User) -> str:
    name = slugify(_player_name(user))
    return f"{user.id}-{name}" if name else str(user.id)


@dataclass
class GameBest:
    game: str
    title: str
    best: str
    rank: int | None


@dataclass
class Profile:
    user: User
    settings: ProfileSettings
    level: int
    xp: int
    current_streak: int
    best_streak: int
    games_played: int
    runs: int
    badges: list[dict] = field(default_factory=list)
    bests: list[GameBest] | None = None  # None: the player hides ranks
    most_played: list[dict] = field(default_factory=list)
    favorites: list[dict] | None = None  # None: the player hides favorites
    wins: int = 0
    tournaments_played: int = 0
    tournament_points: int = 0


def _shown(schema: list[dict], stats: dict | None, best: int) -> str:
    stat = schema[0]
    value = stats_of(schema, stats, best)[stat["key"]]
    if value is None:
        return "-"
    return f"{value // 60}:{value % 60:02d}" if stat["format"] == "time" else f"{value:,}"


def build(db: Session, user: User) -> Profile:
    snap = progress.snapshot(db, user.id)
    st = settings_of(db, user.id)
    owned = {a.key: a for a in progress.awards_of(db, user.id) if a.kind == "badge"}
    earned = [{"key": b.key, "name": b.name, "description": b.description, "earned_at": owned[b.key].earned_at}
              for b in badges.CATALOG if b.key in owned]
    p = Profile(user=user, settings=st, level=snap.level, xp=snap.xp, current_streak=snap.current_streak,
                best_streak=snap.best_streak, games_played=snap.distinct_games, runs=snap.runs, badges=earned,
                wins=snap.tournament_wins)

    t = db.execute(select(func.count(), func.coalesce(func.sum(TournamentPoints.points), 0)).where(TournamentPoints.user_id == user.id)).one()
    p.tournaments_played, p.tournament_points = t[0], int(t[1])

    shown = db.execute(select(Run.game_name, func.count()).join(Game, Game.name == Run.game_name)
                       .where(Run.user_id == user.id, Run.flagged.is_(False), Game.hidden.is_(None))
                       .group_by(Run.game_name).order_by(func.count().desc(), Run.game_name).limit(MOST_PLAYED)).all()
    p.most_played = [{"game": g, "title": _title(g), "runs": n} for g, n in shown]

    if st.show_ranks:
        rows = db.execute(select(Score, Game).join(Game, Game.name == Score.game_name)
                          .where(Score.user_id == user.id, Game.hidden.is_(None))
                          .order_by(Score.updated_at.desc()).limit(BEST_GAMES)).all()
        p.bests = [GameBest(game=g.name, title=_title(g.name), best=_shown(g.leaderboard or DEFAULT_BOARD, s.stats, s.best),
                            rank=rank_of(db, user.id, g.name)) for s, g in rows]

    if st.show_favorites:
        keys = db.scalars(select(Favorite.key).where(Favorite.user_id == user.id, Favorite.key.like("g:%"))
                          .order_by(Favorite.created_at.desc()).limit(FAVORITES)).all()
        live = set(db.scalars(select(Game.name).where(Game.name.in_([k[2:] for k in keys]), Game.hidden.is_(None))).all())
        p.favorites = [{"game": k[2:], "title": _title(k[2:])} for k in keys if k[2:] in live]
    return p
