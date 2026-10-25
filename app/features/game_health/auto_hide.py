"""F8: hide a game that keeps failing, and tell its owner and the super admins.

An unhealthy game (FAILS_IN_A_ROW failed checks) is marked "needs fixing" by start.ng itself: hidden everywhere, its
owner sees why in Your games and says when it's fixed, and an admin checks it and puts it back (the Needs fixing tab).

When many games fail together it's more likely start.ng (the Worker, R2, the API) than the games, so nothing is
hidden: the super admins get one alert an hour instead. "Together" is judged on the round and on every check of the
last CHECK_EVERY, because a round only checks the games that are due. A game that's unhealthy is hidden whenever it's
next checked and nothing holds it back, including games that were unhealthy before this shipped. Needs GAME_HEALTH_AUTO_HIDE and the needs_fixing flag.
"""
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import flags
from ...activity import record
from ...config import settings
from ...models import Activity, Game
from ..email import service as email
from ..email.service import Note, esc
from ..needs_fixing import service as needs_fixing
from ..needs_fixing.models import FixRequest
from .models import GameHealth
from .service import CHECK_EVERY

log = logging.getLogger(__name__)

HIDDEN_BY = needs_fixing.HEALTH_CHECK  # FixRequest.created_by for games hidden here
OUTAGE_SHARE = 0.3  # this share of the games checked failing (in a round, or in the last CHECK_EVERY)...
OUTAGE_MIN_CHECKED = 5  # ...out of at least this many looks like start.ng is down
MAX_HIDDEN_PER_HOUR = 5  # more than this in an hour looks like start.ng too
ALERT_EVERY = timedelta(hours=1)


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def _link(path: str) -> str:
    return settings.site_url.rstrip("/") + path


def _problems_html(problems: list[dict]) -> str:
    return "<ul>" + "".join(f"<li>{esc(p['message'])}</li>" for p in problems) + "</ul>"


def _problems_text(problems: list[dict]) -> str:
    return "\n".join(f"- {p['message']}" for p in problems)


def _button(link: str, label: str) -> str:
    return (f'<p><a href="{esc(link)}" style="display:inline-block;background:#d9f99d;color:#1d1d1b;padding:10px 16px;'
            f'border-radius:10px;font-weight:700;text-decoration:none">{esc(label)}</a></p>')


TRY = ("Check that index.html is at the top of your repo (or in dist/), that every file it loads is in the repo with "
       "exactly the same name (capital letters count), and open the game from a fresh browser tab.")


def render_owner(d: dict) -> Note:
    title = _title(d["game"])
    return Note(
        subject=f"{title} is hidden: start.ng couldn't open it",
        html=(f"<p>start.ng tried to open <b>{esc(title)}</b> three times in a row and it failed each time, so it's hidden "
              f"from start.ng and its own address for now.</p>{_problems_html(d['problems'])}"
              f"<p><b>What to try:</b> {esc(TRY)}</p>"
              f"<p>Push the fix to your repo (start.ng updates by itself), then tell us it's fixed in Your games. "
              f"Our team checks it and puts it back.</p>{_button(_link('/?profile'), 'Tell us it’s fixed')}"),
        text=(f"start.ng tried to open {title} three times in a row and it failed each time, so it's hidden from start.ng "
              f"and its own address for now.\n\n{_problems_text(d['problems'])}\n\nWhat to try: {TRY}\n\n"
              f"Push the fix to your repo (start.ng updates by itself), then tell us it's fixed in Your games: "
              f"{_link('/?profile')}\nOur team checks it and puts it back."),
    )


def render_admin(d: dict) -> Note:
    title = _title(d["game"])
    return Note(
        subject=f"Hidden automatically: {title}",
        html=(f"<p><b>{esc(title)}</b> by {esc(d['owner'])} failed three health checks in a row, so start.ng hid it and "
              f"marked it as needing a fix.</p>{_problems_html(d['problems'])}"
              f"<p>{'Its owner was emailed.' if d['owner_emailed'] else 'Its owner sees it in Your games (no email).'} "
              f"When they say it's fixed, it shows in Needs fixing.</p>{_button(_link('/admin#needs-fixing'), 'Open Needs fixing')}"),
        text=(f"{title} by {d['owner']} failed three health checks in a row, so start.ng hid it and marked it as needing a "
              f"fix.\n\n{_problems_text(d['problems'])}\n\nNeeds fixing: {_link('/admin#needs-fixing')}"),
    )


def render_outage(d: dict) -> Note:
    games = ", ".join(_title(g) for g in d["games"])
    return Note(
        subject="start.ng health checks: many games failing at once",
        html=(f"<p>{d['failed']} of the {d['checked']} games checked just now failed ({esc(games)}). That looks like "
              f"start.ng itself (the Worker, game storage or the API), so <b>nothing was hidden</b>.</p>"
              f"<p>Check that games open, then look at Game health. This alert is sent at most once an hour.</p>"
              f"{_button(_link('/admin#game-health'), 'Open Game health')}"),
        text=(f"{d['failed']} of the {d['checked']} games checked just now failed ({games}). That looks like start.ng "
              f"itself (the Worker, game storage or the API), so nothing was hidden.\n\nCheck that games open, then look "
              f"at Game health: {_link('/admin#game-health')}\nThis alert is sent at most once an hour."),
    )


email.register_kind("game_hidden_unhealthy", None, render_owner)
email.register_kind("admin_game_hidden_unhealthy", None, render_admin)
email.register_kind("admin_health_outage", None, render_outage)


def enabled() -> bool:
    return settings.game_health_auto_hide and flags.is_on("needs_fixing")


def looks_like_outage(db: Session, checked: int, failed: int) -> bool:
    """Many games failing at once: in this round, or in every check of the last CHECK_EVERY (a round only checks the
    games that are due, so in production it's often just a few)."""
    if checked >= OUTAGE_MIN_CHECKED and failed >= checked * OUTAGE_SHARE:
        return True
    since = email.now() - CHECK_EVERY
    rows = db.execute(select(GameHealth.status).join(Game, Game.name == GameHealth.game_name).where(
        GameHealth.checked_at >= since, Game.hidden.is_(None))).scalars().all()
    bad = sum(status in ("failing", "unhealthy") for status in rows)
    return len(rows) >= OUTAGE_MIN_CHECKED and bad >= len(rows) * OUTAGE_SHARE


def hidden_last_hour(db: Session) -> int:
    since = email.now() - timedelta(hours=1)
    return db.scalar(select(func.count()).select_from(FixRequest).where(
        FixRequest.created_by == HIDDEN_BY, FixRequest.created_at >= since)) or 0


def _alert_outage(db: Session, names: list[str], checked: int, failed: int) -> None:
    """Tell the super admins, at most once an hour."""
    since = email.now() - ALERT_EVERY
    if db.scalar(select(Activity.id).where(Activity.action == "game_health.held", Activity.at >= since)):
        return
    record(db, None, "system", "game_health.held", None, checked=checked, failed=failed, games=names[:20])
    email.send_to_admins(db, "admin_health_outage", {"checked": checked, "failed": failed, "games": names[:10]})
    db.commit()
    log.warning("Game health: %s of %s checks failed; looks like start.ng, nothing hidden", failed, checked)


def hide(db: Session, store, game: Game, row: GameHealth) -> bool:
    """Mark one unhealthy game as needing a fix, and email its owner and the super admins. Commits."""
    problems = row.problems or []
    note = "start.ng couldn't open this game 3 times in a row: " + "; ".join(p["message"] for p in problems)
    record(db, None, "system", "game_health.auto_hidden", game.name, problems=[p["code"] for p in problems])
    if needs_fixing.mark(db, store, game, note[:1000], HIDDEN_BY) is None:  # commits
        return False  # an admin marked it already
    row.failures = 0  # if an admin puts it back, it takes 3 new failures to hide it again
    owner_emailed = settings.dev_emails and email.send_to_user(db, game.owner, "game_hidden_unhealthy",
                                                               {"game": game.name, "problems": problems})
    email.send_to_admins(db, "admin_game_hidden_unhealthy", {
        "game": game.name, "owner": game.owner.name or game.owner.email, "problems": problems,
        "owner_emailed": bool(owner_emailed)})
    db.commit()
    log.info("Game health: hid %s after its failed checks", game.name)
    return True


def act(db: Session, store, names: list[str], checked: int, failed: int) -> list[str]:
    """After a round of checks: hide the games in names that are unhealthy and still listed, unless it looks like
    start.ng is down. Returns the games it hid."""
    if not names or not enabled():
        return []
    rows = [(db.get(Game, n), db.get(GameHealth, n)) for n in names]
    todo = [(g, r) for g, r in rows if g is not None and r is not None and r.status == "unhealthy" and g.hidden is None]
    if not todo:
        return []
    if looks_like_outage(db, checked, failed) or hidden_last_hour(db) + len(todo) > MAX_HIDDEN_PER_HOUR:
        _alert_outage(db, [g.name for g, _ in todo], checked, failed)
        return []
    return [g.name for g, r in todo if hide(db, store, g, r)]
