"""Tournament emails (H1). One tournament ends as the next starts (6 PM, start.ng's time), so that's when we write:

  - "tournament_day" to everyone on the tournament topic (visitors too): who won the one that just ended, and the
    new one's games;
  - "tournament_result" to each player of the one that ended who chose results emails: their place and points.

A player who gets both gets one email (the 1-a-day cap merges them). The email sender's loop calls tick(), which also
settles the ended tournament and makes the new one, so this happens even when nobody opens start.ng at 6 PM.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events, flags, tournaments
from ...config import settings
from ...models import Tournament, TournamentPoints
from ...routers.games import _player_name
from . import service
from .models import Announcement
from .service import Note, esc

log = logging.getLogger(__name__)

ANNOUNCE_WITHIN = timedelta(hours=3)  # a tournament that started longer ago than this isn't announced any more


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _local_time(when: datetime) -> str:
    zone = timezone(timedelta(hours=settings.tournament_utc_offset))
    local = tournaments.utc(when).astimezone(zone)
    sign = "+" if settings.tournament_utc_offset >= 0 else "-"
    return f"{local.strftime('%I:%M %p').lstrip('0')} (UTC{sign}{abs(settings.tournament_utc_offset)})"


def _button(link: str, label: str) -> str:
    return (f'<p><a href="{esc(link)}" style="display:inline-block;background:#d9f99d;color:#1d1d1b;padding:10px 16px;'
            f'border-radius:10px;font-weight:700;text-decoration:none">{esc(label)}</a></p>')


def render_day(d: dict) -> Note:
    games = d["games"]
    winner = d.get("winner")
    subject = (f"{winner} won yesterday's tournament. Today: {len(games)} games" if winner
               else f"Today's tournament: {len(games)} games, 24 hours")
    others = f', ahead of {d["players"] - 1} other {"player" if d["players"] == 2 else "players"}' if d["players"] > 1 else ""
    won_html = f"<p><b>{esc(winner)}</b> won the last tournament{others}.</p>" if winner else ""
    won_text = f"{winner} won the last tournament{others}.\n\n" if winner else ""
    html = (f'{won_html}<p style="font-size:18px;font-weight:700;margin:0 0 8px">Today\'s tournament is on</p>'
            f'<p>{len(games)} games, 24 hours, one winner. It ends at {esc(d["ends"])}.</p>'
            f'<ul>{"".join(f"<li>{esc(g)}</li>" for g in games)}</ul>'
            f'{_button(d["link"], "Play today")}<p style="color:#6b6b66">Share it on X to enter, then play.</p>')
    text = (f"{won_text}Today's tournament is on: {len(games)} games, 24 hours, one winner. It ends at {d['ends']}.\n\n"
            + "\n".join(f"- {g}" for g in games) + f"\n\nPlay today: {d['link']}\nShare it on X to enter, then play.")
    return Note(subject, html, text)


def render_result(d: dict) -> Note:
    place, players, points = d["place"], d["players"], d["points"]
    pts = f'{points} {"point" if points == 1 else "points"}'
    if place == 1:
        subject = "You won the start.ng tournament!"
        line = f"You won, with {pts}. Your name is on the past winners list."
    else:
        subject = f"You came {ordinal(place)} of {players} in the tournament"
        line = f"You came {ordinal(place)} of {players}, with {pts}. {esc(d['winner'])} won."
    html = f'<p style="font-size:18px;font-weight:700;margin:0 0 8px">{esc(subject)}</p><p>{line}</p>{_button(d["link"], "Try again today")}'
    return Note(subject, html, f"{subject}\n\n{line}\n\nTry again today: {d['link']}")


service.register_kind("tournament_day", "tournament", render_day)
service.register_kind("tournament_result", "results", render_result)


def _link() -> str:
    return settings.site_url.rstrip("/") + "/tournament"


# ---- results: when a tournament is settled


def _on_finished(payload: dict) -> None:
    if not flags.is_on(service.NAME):
        return
    with service.SessionLocal() as db:
        rows = db.scalars(select(TournamentPoints).where(TournamentPoints.tournament_id == payload["tournament_id"])).all()
        winner = next((r.user for r in rows if r.place == 1), None)
        for r in rows:
            service.notify(r.user_id, "tournament_result", {
                "place": r.place, "players": payload["players"], "points": r.points, "link": _link(),
                "winner": _player_name(winner, "Someone") if winner else "Someone",
            }, db=db)
        db.commit()


events.subscribe("tournament_finished")(_on_finished)


# ---- the day's email: once per tournament, when it starts


def tick(db: Session, at: datetime | None = None) -> bool:
    """Settle the tournament that ended (results go out through tournament_finished), make today's, and announce
    it if it hasn't been. Returns whether it announced one."""
    at = at or tournaments.now()
    t = tournaments.current(db, at)  # also settles ended ones
    if t is None or at - tournaments.utc(t.starts_at) > ANNOUNCE_WITHIN:
        return False
    key = f"tournament:{t.id}"
    if db.get(Announcement, key) is not None:
        return False
    db.add(Announcement(key=key))
    try:
        db.commit()
    except IntegrityError:  # another server announced it
        db.rollback()
        return False
    before = db.scalar(select(Tournament).where(Tournament.ends_at == t.starts_at, Tournament.finished_at.is_not(None)))
    players = 0
    winner = None
    if before is not None and before.winner is not None:
        players = len(db.scalars(select(TournamentPoints.id).where(TournamentPoints.tournament_id == before.id)).all())
        winner = _player_name(before.winner, "Someone")
    service.broadcast("tournament", "tournament_day", {
        "games": [_title(g.game_name) for g in t.games], "ends": _local_time(t.ends_at), "link": _link(),
        "winner": winner, "players": players,
    }, db=db)
    db.commit()
    return True
