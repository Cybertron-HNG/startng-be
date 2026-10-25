"""F10: players report problems with start.ng itself; admins sort them; a real bug pays the reporter coins.

    a signed-in player sends a report  ->  "new", with earlier reports that look like it (for the admins)
    an admin decides                    ->  "confirmed": a real bug, the reporter gets SUPPORT_BUG_REWARD coins
                                            (once per report, at most SUPPORT_REWARDS_PER_DAY paid reports a day)
                                        ->  "duplicate" of an earlier one: a thank-you, no coins
                                        ->  "not_a_bug"
    a confirmed bug is fixed            ->  "fixed"

Nothing is paid before an admin confirms it. Coins go through the wardrobe wallet (needs the wardrobe flag).
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import flags
from ...activity import record
from ...config import settings
from ...models import User
from .models import ProblemReport

NAME = "support"
STATUSES = ("new", "confirmed", "duplicate", "not_a_bug", "fixed")
REWARD_REASON = "bug_report"  # the wallet ledger's reason; ref is the report's id
SIMILAR = 0.34  # word overlap (Jaccard) from which an earlier report counts as looking alike
STOP = set("the a an and or but to of in on at for with is it this that was were be been my i me we you they "
           "not no can cant cannot when then there here have has had just so if from by as are do does did "
           "after before into up down out again still page".split())


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def now() -> datetime:
    return datetime.now(timezone.utc)


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def likeness(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def look_alikes(db: Session, what: str, page: str | None, limit: int = 3, exclude: int | None = None) -> list[int]:
    """Earlier reports (still open or confirmed, last 60 days) that look like this one, best first. The same page
    counts a little extra."""
    since = now() - timedelta(days=60)
    rows = db.scalars(select(ProblemReport).where(
        ProblemReport.status.in_(("new", "confirmed")), ProblemReport.created_at >= since)).all()
    scored = []
    for r in rows:
        if r.id == exclude:
            continue
        score = likeness(what, r.what) + (0.1 if page and r.page == page else 0)
        if score >= SIMILAR:
            scored.append((score, r.id))
    return [i for _, i in sorted(scored, reverse=True)[:limit]]


def clean_page(page: str | None) -> str | None:
    """The page as a path only: no query string or fragment (they can carry tokens or codes)."""
    if not page:
        return None
    path = re.sub(r"^https?://[^/]+", "", page.strip()).split("?")[0].split("#")[0]
    return path[:300] or "/"


def create(db: Session, user: User, what: str, page: str | None, browser: str | None) -> ProblemReport:
    text = " ".join(what.split())
    if len(text) < 10:
        raise Refused("too_short", "Say a little more about what happened (at least 10 characters).")
    since = now() - timedelta(days=1)
    sent = db.scalar(select(func.count()).select_from(ProblemReport).where(
        ProblemReport.user_id == user.id, ProblemReport.created_at >= since)) or 0
    if sent >= settings.support_reports_per_day:
        raise Refused("too_many", "You've sent a few reports today. Thanks! Try again tomorrow.")
    page = clean_page(page)
    report = ProblemReport(user_id=user.id, what=text[:2000], page=page, browser=(browser or "")[:120] or None,
                           similar=look_alikes(db, text, page) or None)
    db.add(report)
    db.flush()
    record(db, user, "player", "support.reported", str(report.id), page=page)
    db.commit()
    return report


def rewarded_today(db: Session, user_id: int) -> int:
    since = now() - timedelta(days=1)
    return db.scalar(select(func.count()).select_from(ProblemReport).where(
        ProblemReport.user_id == user_id, ProblemReport.coins > 0, ProblemReport.decided_at >= since)) or 0


def _pay(db: Session, report: ProblemReport) -> int:
    """Pay for a confirmed bug: once per report, at most SUPPORT_REWARDS_PER_DAY a day, only with the wallet on."""
    if report.coins or not flags.is_on("wardrobe") or settings.support_bug_reward <= 0:
        return 0
    if rewarded_today(db, report.user_id) >= settings.support_rewards_per_day:
        return 0
    from ..wardrobe import service as wardrobe

    paid = wardrobe.pay(db, report.user_id, settings.support_bug_reward, REWARD_REASON, str(report.id))  # commits
    report.coins = paid
    return paid


def decide(db: Session, report: ProblemReport, status: str, by: str, note: str | None = None,
           duplicate_of: int | None = None) -> ProblemReport:
    """An admin sorts a report. Confirming pays the reporter (once). Commits."""
    if status not in STATUSES or status == "new":
        raise Refused("bad_status", "Pick confirmed, duplicate, not a bug or fixed.")
    if status == "fixed" and report.status not in ("confirmed", "fixed"):
        raise Refused("not_confirmed", "Confirm it's a real bug before marking it fixed.")
    if status == "duplicate":
        original = db.get(ProblemReport, duplicate_of) if duplicate_of else None
        if original is None or original.id == report.id:
            raise Refused("duplicate_of", "Say which report it repeats.")
        report.duplicate_of = original.id
    else:
        report.duplicate_of = None
    report.status, report.decided_by, report.decided_at = status, by, now()
    if note is not None:
        report.admin_note = note.strip() or None
    db.commit()
    if status == "confirmed":
        _pay(db, report)
        db.commit()
    return report
