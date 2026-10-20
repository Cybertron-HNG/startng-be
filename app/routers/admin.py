"""The admin page's API (start.ng/admin), and players reporting games. Who can do what: app/admin.py."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import admin as adm
from .. import tournaments
from ..activity import record
from ..admin import AdminUser, current_admin, require, require_super
from ..config import settings
from ..db import get_db
from ..game_store import GameStore, get_game_store
from ..models import Activity, Admin, DeveloperContact, Game, GameReport, Role, Tournament, User
from ..schemas import (
    AdminIn, AdminMe, AdminOut, AdminRoleIn, AdminTournamentOut, AdminTournamentsOut, CloseIn, ContactIn, ContactOut,
    DeveloperOut, FlagIn, GameCard, LogOut, OverviewOut, Permission, ReportIn, ReportOut, RestoreIn, ReviewGameOut, RoleIn,
    RoleOut, TournamentGamesIn,
)
from ..security import current_user
from ..storage import game_url
from .games import _cover_url, _game_or_404, _player_name

router = APIRouter(prefix="/admin", tags=["admin"])
reports_router = APIRouter(prefix="/games", tags=["reports"])


def _details(reason: str, details: str | None) -> str | None:
    details = (details or "").strip() or None
    if reason == "other" and not details:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say what's wrong with the game")
    return details


def _report_out(r: GameReport) -> ReportOut:
    who = (r.reporter.name or r.reporter.email) if r.reporter else None
    return ReportOut(id=r.id, game=r.game_name, reason=r.reason, details=r.details, by_admin=r.by_admin,
                     reporter_id=r.reporter_id, reporter=who, status=r.status, created_at=r.created_at,
                     closed_at=r.closed_at, closed_by=r.closed_by, note=r.note)


log = logging.getLogger(__name__)


def _offline(store: GameStore | None, name: str, hidden: bool) -> None:
    """Take a game's own address offline (the Worker shows an error page) or back online. Without game storage
    (local testing) there's nothing to change."""
    if store is None:
        return
    try:
        store.set_hidden(name, hidden)
    except Exception:  # the database is the record; a storage hiccup shouldn't undo an admin's decision
        log.exception("Couldn't %s %s", "take offline" if hidden else "put back online", name)


def _hide(db: Session, store: GameStore | None, game: Game, why: str) -> None:
    adm.hide(game, why)
    db.commit()
    _offline(store, game.name, True)
    tournaments.replace_hidden(db, game.name)


# ---- players: reporting a game


@reports_router.post("/{name}/report", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def report_game(name: str, body: ReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Report a game (signed in, so admins know who reported it). It stays up until an admin approves the report,
    which hides it. One open report per player per game, and settings.reports_per_day a day."""
    game = _game_or_404(db, name)
    details = _details(body.reason, body.details)
    mine = db.scalar(select(GameReport).where(GameReport.game_name == name, GameReport.reporter_id == user.id,
                                              GameReport.status == "open"))
    if mine:
        return _report_out(mine)
    since = adm.now() - timedelta(days=1)
    sent = db.scalar(select(func.count()).select_from(GameReport).where(GameReport.reporter_id == user.id,
                                                                         GameReport.created_at >= since)) or 0
    if sent >= settings.reports_per_day:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "You've sent a lot of reports today. Try again tomorrow")
    report = GameReport(game_name=name, reporter_id=user.id, by_admin=False, reason=body.reason, details=details)
    db.add(report)
    record(db, user, "player", "game.reported", game.name, reason=body.reason)
    db.commit()
    db.refresh(report)
    return _report_out(report)


# ---- who am I, and the overview


@router.get("/me", response_model=AdminMe)
def admin_me(admin: AdminUser = Depends(current_admin)):
    """The signed-in admin's access. 404 for everyone else (the website then shows no admin buttons)."""
    return AdminMe(email=admin.email, is_super=admin.is_super, role=admin.role, permissions=sorted(admin.permissions))


@router.get("/overview", response_model=OverviewOut)
def overview(admin: AdminUser = Depends(current_admin), db: Session = Depends(get_db)):
    count = lambda q: db.scalar(select(func.count()).select_from(q.subquery())) or 0  # noqa: E731
    return OverviewOut(
        open_reports=count(select(GameReport.id).where(GameReport.status == "open")),
        hidden_games=count(select(Game.name).where(Game.hidden.is_not(None))),
        games_waiting=count(select(GameReport.game_name).where(GameReport.status == "open").distinct()),
        blocked_developers=count(select(User.id).where(User.blocked_at.is_not(None))),
        admins=count(select(Admin.id)) + len(adm.super_admins()),
    )


@router.get("/permissions", response_model=list[Permission])
def permissions(admin: AdminUser = Depends(current_admin)):
    return [Permission(key=k, label=v) for k, v in adm.PERMISSIONS.items()]


# ---- roles and admins (super admins only)


def _role_out(db: Session, role: Role) -> RoleOut:
    n = db.scalar(select(func.count()).select_from(Admin).where(Admin.role_id == role.id)) or 0
    return RoleOut(id=role.id, name=role.name, permissions=list(role.permissions), admins=n)


def _check_role(db: Session, body: RoleIn, role_id: int | None = None) -> tuple[str, list[str]]:
    name = body.name.strip()
    unknown = [p for p in body.permissions if p not in adm.PERMISSIONS]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown permissions: {', '.join(unknown)}")
    taken = db.scalar(select(Role.id).where(func.lower(Role.name) == name.lower()))
    if taken and taken != role_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already a role with that name")
    return name, sorted(set(body.permissions))


@router.get("/roles", response_model=list[RoleOut])
def list_roles(admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    return [_role_out(db, r) for r in db.scalars(select(Role).order_by(Role.name)).all()]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(body: RoleIn, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    name, perms = _check_role(db, body)
    role = Role(name=name, permissions=perms)
    db.add(role)
    adm.log(db, admin, "role.create", name, permissions=perms)
    db.commit()
    return _role_out(db, role)


def _role_or_404(db: Session, role_id: int) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such role")
    return role


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: int, body: RoleIn, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    role = _role_or_404(db, role_id)
    role.name, role.permissions = _check_role(db, body, role_id)
    adm.log(db, admin, "role.update", role.name, permissions=role.permissions)
    db.commit()
    return _role_out(db, role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: int, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    """Admins who had it keep their place but can do nothing until they're given another role."""
    role = _role_or_404(db, role_id)
    adm.log(db, admin, "role.delete", role.name)
    db.delete(role)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _admin_out(db: Session, row: Admin | None, email: str | None = None) -> AdminOut:
    email = row.email if row else email
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    return AdminOut(id=row.id if row else None, email=email, is_super=row is None,
                    role_id=row.role_id if row else None, role=(row.role.name if row.role else None) if row else "Super admin",
                    added_by=row.added_by if row else None, created_at=row.created_at if row else None,
                    signed_up=bool(user and user.google_sub))


@router.get("/admins", response_model=list[AdminOut])
def list_admins(admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    supers = [_admin_out(db, None, e) for e in sorted(adm.super_admins())]
    return supers + [_admin_out(db, a) for a in db.scalars(select(Admin).order_by(Admin.created_at)).all()]


@router.post("/admins", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
def add_admin(body: AdminIn, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    """Add an admin by email. They sign in to start.ng with Google as that email; there's no admin sign-up."""
    email = body.email.lower()
    if email in adm.super_admins() or db.scalar(select(Admin.id).where(Admin.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "They're already an admin")
    if body.role_id is not None:
        _role_or_404(db, body.role_id)
    row = Admin(email=email, role_id=body.role_id, added_by=admin.email)
    db.add(row)
    adm.log(db, admin, "admin.add", email, role_id=body.role_id)
    db.commit()
    db.refresh(row)
    return _admin_out(db, row)


def _admin_row(db: Session, admin_id: int) -> Admin:
    row = db.get(Admin, admin_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such admin")
    return row


@router.put("/admins/{admin_id}", response_model=AdminOut)
def set_admin_role(admin_id: int, body: AdminRoleIn, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    row = _admin_row(db, admin_id)
    if body.role_id is not None:
        _role_or_404(db, body.role_id)
    row.role_id = body.role_id
    adm.log(db, admin, "admin.role", row.email, role_id=body.role_id)
    db.commit()
    db.refresh(row)
    return _admin_out(db, row)


@router.delete("/admins/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin(admin_id: int, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    row = _admin_row(db, admin_id)
    adm.log(db, admin, "admin.remove", row.email)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- reports, flags and hidden games


@router.get("/reports", response_model=list[ReportOut])
def list_reports(
    state: str = Query("open", pattern="^(open|approved|dismissed|all)$"),
    game: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    admin: AdminUser = Depends(require("reports.view")),
    db: Session = Depends(get_db),
):
    q = select(GameReport).order_by(GameReport.created_at.desc()).limit(limit)
    if state != "all":
        q = q.where(GameReport.status == state)
    if game:
        q = q.where(GameReport.game_name == game)
    return [_report_out(r) for r in db.scalars(q).all()]


def _card(g: Game) -> GameCard:
    return GameCard(name=g.name, url=game_url(g.name), cover_url=_cover_url(g), by=_player_name(g.owner, "A builder"),
                    description=g.description, hidden=g.hidden)


@router.get("/games/review", response_model=list[ReviewGameOut])
def games_to_review(admin: AdminUser = Depends(require("reports.view")), db: Session = Depends(get_db)):
    """Games with reports waiting for a decision (still up), then hidden games, with their reports and how often
    their developer has been contacted."""
    waiting = select(GameReport.game_name).where(GameReport.status == "open")
    games = db.scalars(select(Game).where(Game.name.in_(waiting) | Game.hidden.is_not(None))).all()
    out = []
    for g in games:
        reports = db.scalars(select(GameReport).where(GameReport.game_name == g.name).order_by(GameReport.created_at.desc())).all()
        contacts = db.scalar(select(func.count()).select_from(DeveloperContact).where(DeveloperContact.developer_id == g.owner_id)) or 0
        out.append(ReviewGameOut(
            **_card(g).model_dump(), hidden_at=g.hidden_at,
            developer_id=g.owner_id, developer=_player_name(g.owner, g.owner.email), developer_contacts=contacts,
            open_reports=sum(r.status == "open" for r in reports), reports=[_report_out(r) for r in reports],
        ))
    # waiting first (most reports first), then hidden ones, newest first
    out.sort(key=lambda r: (r.hidden is not None, -r.open_reports, -(r.hidden_at.timestamp() if r.hidden_at else 0)))
    return out


@router.post("/games/{name}/flag", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def flag_game(name: str, body: FlagIn, admin: AdminUser = Depends(require("games.flag")), db: Session = Depends(get_db),
              store: GameStore | None = Depends(get_game_store)):
    """An admin flags a game (from the admin page or the game's own page). It's hidden at once."""
    game = _game_or_404(db, name)
    report = GameReport(game_name=name, reporter_id=admin.user.id, by_admin=True, reason=body.reason,
                        details=_details(body.reason, body.details), status="approved",
                        closed_at=adm.now(), closed_by=admin.email)
    db.add(report)
    adm.log(db, admin, "game.flagged", name, reason=body.reason)
    _hide(db, store, game, adm.FLAGGED)
    db.refresh(report)
    return _report_out(report)


@router.post("/reports/{report_id}/close", response_model=ReportOut)
def close_report(report_id: int, body: CloseIn, admin: AdminUser = Depends(require("reports.close")), db: Session = Depends(get_db),
                 store: GameStore | None = Depends(get_game_store)):
    """Approve a report, which hides the game, or dismiss it (the game stays up)."""
    report = db.get(GameReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report")
    if report.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"That report was already {report.status}")
    report.status, report.note = body.outcome, body.note
    report.closed_at, report.closed_by = adm.now(), admin.email
    adm.log(db, admin, f"report.{body.outcome}", report.game_name, report_id=report.id, reason=report.reason)
    if body.outcome == "approved":
        _hide(db, store, report.game, adm.REPORTED)
    else:
        db.commit()
    return _report_out(report)


@router.post("/games/{name}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_game(name: str, body: RestoreIn, admin: AdminUser = Depends(require("reports.close")), db: Session = Depends(get_db),
                 store: GameStore | None = Depends(get_game_store)):
    """Put a hidden game back on start.ng. Reports still open on it are dismissed. A blocked developer's
    games come back only when the developer is unblocked."""
    game = _game_or_404(db, name)
    if game.owner.blocked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Its developer is blocked. Unblock them to put their games back")
    for r in db.scalars(select(GameReport).where(GameReport.game_name == name, GameReport.status == "open")).all():
        r.status, r.note, r.closed_at, r.closed_by = "dismissed", body.note, adm.now(), admin.email
    adm.unhide(game)
    adm.log(db, admin, "game.restored", name, note=body.note)
    db.commit()
    _offline(store, name, False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- developers: contacting, blocking, deleting


def _developer(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such developer")
    return user


def _contacts(db: Session, user_id: int) -> list[DeveloperContact]:
    return list(db.scalars(select(DeveloperContact).where(DeveloperContact.developer_id == user_id)
                           .order_by(DeveloperContact.created_at.desc())).all())


def _developer_out(db: Session, user: User) -> DeveloperOut:
    contacts = _contacts(db, user.id)
    games = db.scalars(select(Game.name).where(Game.owner_id == user.id).order_by(Game.name)).all()
    return DeveloperOut(
        id=user.id, email=user.email, name=user.name, blocked_at=user.blocked_at, games=list(games),
        contacts=[ContactOut(id=c.id, issue=c.issue, game_name=c.game_name, report_id=c.report_id,
                             admin_email=c.admin_email, created_at=c.created_at) for c in contacts],
        contacts_needed=settings.contacts_before_block,
        can_block=len(contacts) >= settings.contacts_before_block,
    )


def _not_protected(db: Session, user: User) -> None:
    """Admins (and start.ng's own system account) are never blocked or deleted from the admin page."""
    email = user.email.lower()
    if email in adm.super_admins() or db.scalar(select(Admin.id).where(Admin.email == email)) or email.endswith("@start.invalid"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Admins and start.ng's own account can't be blocked or deleted")


def _enough_contacts(db: Session, user: User) -> None:
    if len(_contacts(db, user.id)) < settings.contacts_before_block:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Reach out to the developer about {settings.contacts_before_block} different issues first")


@router.get("/developers/{user_id}", response_model=DeveloperOut)
def developer(user_id: int, admin: AdminUser = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return _developer_out(db, _developer(db, user_id))


@router.post("/developers/{user_id}/contacts", response_model=DeveloperOut, status_code=status.HTTP_201_CREATED)
def add_contact(user_id: int, body: ContactIn, admin: AdminUser = Depends(require("developers.contact")), db: Session = Depends(get_db)):
    """Record that an admin reached out to the developer about an issue (start.ng doesn't send anything itself)."""
    user = _developer(db, user_id)
    if body.report_id is not None:
        report = db.get(GameReport, body.report_id)
        if report is None or report.game.owner_id != user.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That report isn't about this developer's games")
        if db.scalar(select(DeveloperContact.id).where(DeveloperContact.report_id == body.report_id)):
            raise HTTPException(status.HTTP_409_CONFLICT, "They've already been contacted about that report")
    db.add(DeveloperContact(developer_id=user.id, game_name=body.game_name, report_id=body.report_id,
                            issue=body.issue.strip(), admin_email=admin.email))
    adm.log(db, admin, "developer.contact", user.email, game=body.game_name, report_id=body.report_id)
    db.commit()
    return _developer_out(db, user)


@router.post("/developers/{user_id}/block", response_model=DeveloperOut)
def block_developer(user_id: int, admin: AdminUser = Depends(require("developers.block")), db: Session = Depends(get_db),
                    store: GameStore | None = Depends(get_game_store)):
    """Block a developer: they can't sign in or publish, and all their games are hidden."""
    user = _developer(db, user_id)
    _not_protected(db, user)
    _enough_contacts(db, user)
    user.blocked_at = adm.now()
    games = db.scalars(select(Game).where(Game.owner_id == user.id)).all()
    for g in games:
        adm.hide(g, adm.BLOCKED)
    adm.log(db, admin, "developer.block", user.email, games=[g.name for g in games])
    db.commit()
    for g in games:
        _offline(store, g.name, True)
        tournaments.replace_hidden(db, g.name)
    return _developer_out(db, user)


@router.post("/developers/{user_id}/unblock", response_model=DeveloperOut)
def unblock_developer(user_id: int, admin: AdminUser = Depends(require("developers.block")), db: Session = Depends(get_db),
                      store: GameStore | None = Depends(get_game_store)):
    """Unblock a developer. Their games come back; flag any that should stay hidden."""
    user = _developer(db, user_id)
    user.blocked_at = None
    back = list(db.scalars(select(Game).where(Game.owner_id == user.id, Game.hidden == adm.BLOCKED)).all())
    for g in back:
        adm.unhide(g)
    adm.log(db, admin, "developer.unblock", user.email)
    db.commit()
    for g in back:
        _offline(store, g.name, False)
    return _developer_out(db, user)


@router.delete("/developers/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_developer(
    user_id: int,
    admin: AdminUser = Depends(require("developers.delete")),
    db: Session = Depends(get_db),
    store: GameStore | None = Depends(get_game_store),
):
    """Delete a developer's account and every game of theirs (files, scores, plays). Can't be undone."""
    user = _developer(db, user_id)
    _not_protected(db, user)
    _enough_contacts(db, user)
    games = list(db.scalars(select(Game).where(Game.owner_id == user.id)).all())
    if games and store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Game storage isn't set up, so their games can't be removed")
    for g in games:
        store.delete_game(g.name)
        db.delete(g)
    adm.log(db, admin, "developer.delete", user.email, games=[g.name for g in games])
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- tournaments: the current one and the next, before players see them


def _tournament_out(db: Session, t: Tournament) -> AdminTournamentOut:
    return AdminTournamentOut(id=t.id, starts_at=tournaments.utc(t.starts_at), ends_at=tournaments.utc(t.ends_at),
                              started=tournaments.started(t), games=[_card(tg.game) for tg in t.games])


def _tournaments_out(db: Session) -> AdminTournamentsOut:
    eligible = tournaments.eligible_games(db)
    games = {g.name: g for g in db.scalars(select(Game).where(Game.name.in_(eligible))).all()} if eligible else {}
    return AdminTournamentsOut(
        tournaments=[_tournament_out(db, t) for t in tournaments.ahead(db)],
        eligible=[_card(games[n]) for n in eligible if n in games],
        min_games=settings.tournament_min_games,
        max_games=max(settings.tournament_games, settings.tournament_min_games),
    )


@router.get("/tournaments", response_model=AdminTournamentsOut)
def admin_tournaments(admin: AdminUser = Depends(require("tournaments.view")), db: Session = Depends(get_db)):
    """The current tournament and the next one (made now if needed), and the games a tournament can use."""
    return _tournaments_out(db)


def _unstarted(db: Session, tournament_id: int) -> Tournament:
    t = db.get(Tournament, tournament_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such tournament")
    if tournaments.started(t):
        raise HTTPException(status.HTTP_409_CONFLICT, "It has started, so its games can't change")
    return t


@router.put("/tournaments/{tournament_id}/games", response_model=AdminTournamentsOut)
def set_tournament_games(tournament_id: int, body: TournamentGamesIn,
                         admin: AdminUser = Depends(require("tournaments.edit")), db: Session = Depends(get_db)):
    """Choose the games of a tournament that hasn't started, from the eligible games."""
    t = _unstarted(db, tournament_id)
    names = list(dict.fromkeys(body.games))
    low, high = settings.tournament_min_games, max(settings.tournament_games, settings.tournament_min_games)
    if not low <= len(names) <= high:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Pick {low} to {high} games")
    allowed = set(tournaments.eligible_games(db))
    wrong = [n for n in names if n not in allowed]
    if wrong:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"These can't be in a tournament: {', '.join(wrong)}")
    adm.log(db, admin, "tournament.games", str(t.id), before=[tg.game_name for tg in t.games], after=names)
    tournaments.set_games(db, t, names)
    return _tournaments_out(db)


@router.post("/tournaments/{tournament_id}/reroll", response_model=AdminTournamentsOut)
def reroll_tournament(tournament_id: int, admin: AdminUser = Depends(require("tournaments.edit")), db: Session = Depends(get_db)):
    """Pick new random games for a tournament that hasn't started."""
    t = _unstarted(db, tournament_id)
    names = tournaments.pick_games(db, tournaments.utc(t.starts_at))
    adm.log(db, admin, "tournament.reroll", str(t.id), before=[tg.game_name for tg in t.games], after=names)
    tournaments.set_games(db, t, names)
    return _tournaments_out(db)


# ---- the activity log


@router.get("/log", response_model=list[LogOut])
def activity_log(
    limit: int = Query(100, ge=1, le=500),
    before: int | None = None,
    kind: str | None = Query(None, pattern="^(player|developer|admin|system)$"),
    admin: AdminUser = Depends(require("log.view")),
    db: Session = Depends(get_db),
):
    """Everything that happened on start.ng, newest first: accounts, games, reports, tournaments and admin actions.
    kind narrows it to who did it; before (an id) pages back."""
    q = select(Activity).order_by(Activity.id.desc()).limit(limit)
    if before:
        q = q.where(Activity.id < before)
    if kind:
        q = q.where(Activity.kind == kind)
    return [LogOut(id=r.id, actor=r.actor, kind=r.kind, action=r.action, target=r.target, detail=r.detail, at=r.at)
            for r in db.scalars(q).all()]
