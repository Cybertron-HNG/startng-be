"""F10 problem reports. Players (signed in): /support/reports. Admins: /admin/support/reports. 404 while the flag is
off."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...config import settings
from ...db import get_db
from ...errors import ApiError
from ...models import User
from ...routers.games import _player_name
from ...security import current_user
from . import service
from .models import ProblemReport

router = APIRouter(tags=["support"])
manage = require("support.reports")


class ReportIn(BaseModel):
    what: str = Field(min_length=1, max_length=2000)
    page: str | None = Field(None, max_length=600)
    browser: str | None = Field(None, max_length=200)


class MyReport(BaseModel):
    id: int
    what: str
    page: str | None
    status: str
    admin_note: str | None
    coins: int
    created_at: datetime
    decided_at: datetime | None


class Similar(BaseModel):
    id: int
    what: str
    status: str


class AdminReport(MyReport):
    reporter: str
    reporter_email: str
    browser: str | None
    similar: list[Similar]
    duplicate_of: int | None
    decided_by: str | None


class Queue(BaseModel):
    counts: dict[str, int]
    reward: int  # coins for a confirmed bug
    per_day: int  # paid reports per player a day
    reports: list[AdminReport]


class DecideIn(BaseModel):
    status: str
    note: str | None = Field(None, max_length=1000)
    duplicate_of: int | None = None


def _mine(r: ProblemReport) -> MyReport:
    return MyReport(id=r.id, what=r.what, page=r.page, status=r.status, admin_note=r.admin_note, coins=r.coins,
                    created_at=r.created_at, decided_at=r.decided_at)


def _admin(db: Session, r: ProblemReport) -> AdminReport:
    similar = [db.get(ProblemReport, i) for i in (r.similar or [])]
    return AdminReport(**_mine(r).model_dump(), reporter=_player_name(r.user, r.user.email), reporter_email=r.user.email,
                       browser=r.browser, duplicate_of=r.duplicate_of, decided_by=r.decided_by,
                       similar=[Similar(id=s.id, what=s.what[:160], status=s.status) for s in similar if s is not None])


def _refuse(e: service.Refused) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, e.code, e.message)


@router.post("/support/reports", response_model=MyReport, status_code=status.HTTP_201_CREATED)
def report(body: ReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Report a problem with start.ng (signed in). An admin looks at it; a real bug earns coins."""
    try:
        return _mine(service.create(db, user, body.what, body.page, body.browser))
    except service.Refused as e:
        raise _refuse(e)


@router.get("/support/reports/mine", response_model=list[MyReport])
def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ProblemReport).where(ProblemReport.user_id == user.id)
                      .order_by(ProblemReport.created_at.desc()).limit(20)).all()
    return [_mine(r) for r in rows]


@router.get("/admin/support/reports", response_model=Queue)
def queue(status_: str | None = Query(None, alias="status"), admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Reports, newest first (the last 200); "new" ones first when no status is picked."""
    q = select(ProblemReport).order_by(ProblemReport.created_at.desc()).limit(200)
    if status_:
        q = q.where(ProblemReport.status == status_)
    rows = db.scalars(q).all()
    if not status_:
        rows = sorted(rows, key=lambda r: r.status != "new")
    counts = dict(db.execute(select(ProblemReport.status, func.count()).group_by(ProblemReport.status)).all())
    return Queue(counts=counts, reward=settings.support_bug_reward, per_day=settings.support_rewards_per_day,
                 reports=[_admin(db, r) for r in rows])


@router.post("/admin/support/reports/{report_id}", response_model=AdminReport)
def decide(report_id: int, body: DecideIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Sort a report: confirmed (a real bug: pays the reporter once), duplicate (of another report), not_a_bug, fixed."""
    r = db.get(ProblemReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report")
    adm.log(db, admin, f"support.{body.status}"[:40], str(report_id))
    try:
        service.decide(db, r, body.status, admin.email, body.note, body.duplicate_of)
    except service.Refused as e:
        db.rollback()
        raise _refuse(e)
    return _admin(db, r)
