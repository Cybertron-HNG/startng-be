"""F9: admins make private tournaments to share in a group (/admin/private-tournaments, permission
tournaments.private). Players register through the same invite link and page as a tournament with friends."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...config import settings
from ...db import get_db
from ...errors import ApiError
from . import service
from .models import PrivateTournament
from .schemas import AdminTournamentIn, AdminTournamentOut

router = APIRouter(prefix="/admin/private-tournaments", tags=["private tournaments"])
manage = require("tournaments.private")


def _out(db: Session, t: PrivateTournament) -> AdminTournamentOut:
    from .router import _summary  # the same summary players get, plus what only admins see

    info = _summary(db, t, None)
    return AdminTournamentOut(**info, link=f"{settings.site_url.rstrip('/')}/friends/{t.code}",
                              game_names=[g.game_name for g in t.games])


def _admin_made(db: Session, code: str) -> PrivateTournament:
    t = service.find(db, code)
    if t is None or not t.by_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no admin tournament with that link")
    return t


@router.get("", response_model=list[AdminTournamentOut])
def list_admin_made(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    return [_out(db, t) for t in service.admin_made(db)]


@router.post("", response_model=AdminTournamentOut, status_code=status.HTTP_201_CREATED)
def create(body: AdminTournamentIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    try:
        t = service.create_for_admin(db, admin.user, body.title, body.games, body.starts_at, body.ends_at, body.max_members)
    except service.Refused as e:
        raise ApiError(status.HTTP_400_BAD_REQUEST, e.code, e.message)
    return _out(db, t)


@router.post("/{code}/new-link", response_model=AdminTournamentOut)
def new_link(code: str, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """A new link: the old one stops working; players who registered stay in."""
    t = _admin_made(db, code)
    service.new_code(db, t, admin.user)
    return _out(db, t)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete(code: str, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    t = _admin_made(db, code)
    adm.log(db, admin, "private_tournament.deleted", t.code, title=t.title)
    db.delete(t)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
