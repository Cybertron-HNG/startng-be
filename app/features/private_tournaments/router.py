from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from ... import leaderboard as boards
from ... import tournaments
from ...db import get_db
from ...errors import ApiError
from ...models import User
from ...routers.games import _cover_url, _player_name
from ...security import current_user, optional_user
from ...storage import game_url
from ...routers import pages
from . import service, share
from .models import PrivateTournament
from .schemas import GameOut, PrivateTournamentIn, PrivateTournamentOut, RunOut, StandingOut, SummaryOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/private-tournaments", tags=["private tournaments"])

TOP_RUNS = 10


def _color(user: User) -> str | None:
    return ((user.character or {}).get("style") or {}).get("primaryColor")


def _standing(st: service.Standing) -> StandingOut:
    return StandingOut(place=st.place, player_id=st.user.id, name=_player_name(st.user), color=_color(st.user), points=st.points, firsts=st.firsts)


def _summary(db: Session, t: PrivateTournament, user: User | None) -> dict:
    joined = bool(user) and service.is_member(db, t, user.id)
    return dict(
        code=t.code, title=t.title, owner=_player_name(t.owner), starts_at=tournaments.utc(t.starts_at), ends_at=tournaments.utc(t.ends_at),
        finished=service.ended(t), members=service.member_count(db, t),
        games=[] if t.by_admin and not joined else [g.game_name for g in t.games],  # F9: register to see the games
        joined=joined, mine=bool(user) and t.owner_id == user.id, by_admin=bool(t.by_admin), max_members=t.max_members,
    )


def _found(db: Session, code: str) -> PrivateTournament:
    t = service.find(db, code)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no tournament with that link")
    return t


def _refuse(e: service.Refused) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, e.code, e.message)


@router.post("", response_model=SummaryOut, status_code=status.HTTP_201_CREATED)
def create(body: PrivateTournamentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        t = service.create(db, user, body.title, body.games, body.hours)
    except service.Refused as e:
        raise _refuse(e)
    return SummaryOut(**_summary(db, t, user))


@router.get("/mine", response_model=list[SummaryOut])
def my_tournaments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [SummaryOut(**_summary(db, t, user)) for t in service.mine(db, user)]


@router.get("/{code}", response_model=PrivateTournamentOut | SummaryOut)
def get_one(code: str, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """Anyone with the link sees the title, games, time and member count. Only members see the boards and standings."""
    t = _found(db, code)
    info = _summary(db, t, user)
    if not info["joined"]:
        return SummaryOut(**info)
    res = service.results(db, t)
    game_boards = []
    for tg in t.games:
        schema = tg.game.leaderboard or boards.DEFAULT
        runs = res.boards.get(tg.game_name, [])
        game_boards.append(GameOut(
            name=tg.game_name, url=game_url(tg.game_name), cover_url=_cover_url(tg.game), leaderboard=schema, players=len(runs),
            top=[RunOut(rank=i, player_id=s.user_id, name=_player_name(s.user), color=_color(s.user),
                        stats=boards.stats_of(schema, s.stats, s.best), points=tournaments.points_for(i))
                 for i, s in enumerate(runs[:TOP_RUNS], start=1)],
        ))
    mine_ = next((st for st in res.standings if st.user.id == user.id), None)
    return PrivateTournamentOut(**info, game_boards=game_boards, standings=[_standing(st) for st in res.standings],
                                me=_standing(mine_) if mine_ else None)


CODE = Path(pattern=r"^[\w-]{8,16}$")


@router.get("/{code}/card.png", response_class=Response)
def card(code: str = CODE, db: Session = Depends(get_db)):
    """The preview picture of an invite link (1200x630)."""
    t = _found(db, code)
    png = share.card_png(t, service.member_count(db, t), _player_name(t.owner))
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


@router.get("/{code}/page", response_class=Response)
def preview_page(code: str = CODE, db: Session = Depends(get_db)):
    """The invite page with the tournament's name already in its tags, for chat link previews (nginx asks for it)."""
    shell = pages._get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    t = service.find(db, code)
    if t is None:
        body = '<main><h1>Tournament not found</h1><p>There is no tournament at this link.</p><p><a href="/friends">Start your own</a></p></main>'
        page = pages._page(shell, title="Tournament not found · start.ng", description="There's no tournament at this link.",
                           path=f"/friends/{code}", image=pages._site() + pages.SHARE_IMAGE, body=body, noindex=True)
        return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
    page = share.page_html(shell, t, service.member_count(db, t), _player_name(t.owner))
    return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


@router.post("/{code}/new-link", response_model=SummaryOut)
def new_link(code: str = CODE, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The maker swaps the invite link for a new one: the old link stops working, members stay in."""
    t = _found(db, code)
    if t.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the player who made it can change its link")
    service.new_code(db, t, user)
    return SummaryOut(**_summary(db, t, user))


@router.post("/{code}/join", response_model=SummaryOut)
def join(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _found(db, code)
    try:
        service.join(db, t, user)
    except service.Refused as e:
        raise _refuse(e)
    return SummaryOut(**_summary(db, t, user))


@router.delete("/{code}/members/me", status_code=status.HTTP_204_NO_CONTENT)
def leave(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        service.leave(db, _found(db, code), user)
    except service.Refused as e:
        raise _refuse(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _found(db, code)
    if t.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the player who made it can delete it")
    db.delete(t)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
