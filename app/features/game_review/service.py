"""F2: a new game waits for an admin before it's listed on start.ng.

    published (new)      ->  pending: unlisted (off lists, search, tournaments), its address still works so the
                             admin and its owner can open it; the owner and the super admins are emailed
    an admin approves    ->  listed; the owner is emailed
    an admin rejects     ->  stays unlisted; the owner is emailed why
    the owner pushes     ->  a rejected game is pending again (auto-update or a new publish)

Only the first publish is reviewed: updates to an approved game go live straight away. A game with no review row is
approved (games live before reviews existed, and games published while the flag was off). Games published by an
admin who can review are approved straight away.
"""
import logging

from sqlalchemy.orm import Session

from ... import admin as adm
from ... import events, flags, tournaments
from ...activity import record
from ...config import settings
from ...db import SessionLocal
from ...models import Game, User
from ...storage import game_url
from ..email import service as email
from ..email.service import Note, esc
from .models import GameReview

log = logging.getLogger(__name__)

NAME = "game_review"
HIDDEN = "in_review"  # games.hidden while it waits, or after it was rejected
adm.register_hide_reason(HIDDEN)

REJECT_REASONS: dict[str, str] = {
    "doesnt_load": "It doesn't load, or it crashes",
    "not_a_game": "It isn't a playable game yet",
    "controls": "The controls don't work (or don't work on phones)",
    "copy": "It's a copy of another game",
    "not_allowed": "It has content that isn't allowed on start.ng",
    "other": "Something else (see the note)",
}


# ---- emails


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def _site(path: str) -> str:
    return settings.site_url.rstrip("/") + path


def _button(link: str, label: str) -> str:
    return (f'<p><a href="{esc(link)}" style="display:inline-block;background:#d9f99d;color:#1d1d1b;padding:10px 16px;'
            f'border-radius:10px;font-weight:700;text-decoration:none">{esc(label)}</a></p>')


def _received(d: dict) -> Note:
    title = _title(d["game"])
    return Note(
        subject=f"We got {title}: it's in review",
        html=(f"<p>Thanks for publishing <b>{esc(title)}</b>. A start.ng admin looks at every new game before it's listed. "
              f"We'll email you when it's done.</p><p>Until then it isn't on start.ng's lists, but its address works, so "
              f"you can keep testing it: <a href=\"{esc(d['url'])}\">{esc(d['url'])}</a></p>"),
        text=(f"Thanks for publishing {title}. A start.ng admin looks at every new game before it's listed. We'll email "
              f"you when it's done.\n\nUntil then it isn't on start.ng's lists, but its address works, so you can keep "
              f"testing it: {d['url']}"),
    )


def _approved(d: dict) -> Note:
    title = _title(d["game"])
    return Note(
        subject=f"{title} is live on start.ng",
        html=(f"<p>Good news: <b>{esc(title)}</b> was approved and is now listed on start.ng.</p>"
              f"{_button(d['url'], 'Play it')}<p>From now on, every push to your repo updates it straight away.</p>"),
        text=(f"Good news: {title} was approved and is now listed on start.ng.\n\nPlay it: {d['url']}\n\n"
              f"From now on, every push to your repo updates it straight away."),
    )


def _rejected(d: dict) -> Note:
    title = _title(d["game"])
    note_html = f"<p><b>From our team:</b> {esc(d['note'])}</p>" if d.get("note") else ""
    note_text = f"\nFrom our team: {d['note']}\n" if d.get("note") else ""
    return Note(
        subject=f"{title} wasn't approved yet",
        html=(f"<p>A start.ng admin looked at <b>{esc(title)}</b> and couldn't list it yet.</p>"
              f"<p><b>Why:</b> {esc(REJECT_REASONS.get(d['reason'], d['reason']))}</p>{note_html}"
              f"<p>Fix it and push to your repo: start.ng updates it and it goes back in the queue by itself.</p>"
              f"{_button(_site('/?profile'), 'See it in Your games')}"),
        text=(f"A start.ng admin looked at {title} and couldn't list it yet.\n\nWhy: "
              f"{REJECT_REASONS.get(d['reason'], d['reason'])}\n{note_text}\nFix it and push to your repo: start.ng "
              f"updates it and it goes back in the queue by itself.\n\nYour games: {_site('/?profile')}"),
    )


def _to_review(d: dict) -> Note:
    title = _title(d["game"])
    again = " (sent again after a fix)" if d.get("again") else ""
    return Note(
        subject=f"New game to review: {title}{again}",
        html=(f"<p><b>{esc(title)}</b> by {esc(d['owner'])} is waiting for review{esc(again)}.</p>"
              f"{_button(_site('/admin#game-review'), 'Open Game review')}"),
        text=f"{title} by {d['owner']} is waiting for review{again}.\n\nGame review: {_site('/admin#game-review')}",
    )


email.register_kind("game_review_received", None, _received)
email.register_kind("game_review_approved", None, _approved)
email.register_kind("game_review_rejected", None, _rejected)
email.register_kind("game_review_waiting", None, _to_review)


def _tell_owner(db: Session, owner: User, kind: str, data: dict) -> None:
    if settings.dev_emails:
        email.send_to_user(db, owner, kind, data)


# ---- the steps


def unlist(db: Session, game: Game) -> None:
    """Off start.ng's lists, search and tournaments; its own address keeps working (so it can be reviewed). Commits."""
    adm.hide(game, HIDDEN)
    db.commit()
    tournaments.replace_hidden(db, game.name)


def submit(db: Session, game: Game) -> GameReview | None:
    """A new game was published: it waits for review, unless an admin who can review published it. Commits."""
    owner = game.owner
    access = adm.admin_of(db, owner)
    if access is not None and access.can("games.review"):
        db.add(GameReview(game_name=game.name, status="approved", decided_by=owner.email, decided_at=adm.now()))
        db.commit()
        return None
    review = GameReview(game_name=game.name, status="pending")
    db.add(review)
    record(db, None, "system", "game_review.submitted", game.name)
    unlist(db, game)  # commits
    _tell_owner(db, owner, "game_review_received", {"game": game.name, "url": game_url(game.name)})
    email.send_to_admins(db, "game_review_waiting", {"game": game.name, "owner": owner.name or owner.email})
    db.commit()
    return review


def resubmit(db: Session, review: GameReview) -> None:
    """The owner pushed a new version of a rejected game: back in the queue. Commits."""
    review.status, review.rounds, review.submitted_at = "pending", review.rounds + 1, adm.now()
    record(db, None, "system", "game_review.resubmitted", review.game_name)
    email.send_to_admins(db, "game_review_waiting", {
        "game": review.game_name, "owner": review.game.owner.name or review.game.owner.email, "again": True})
    db.commit()


def approve(db: Session, store, review: GameReview, by: str) -> None:
    """Listed (unless it's also hidden for a stronger reason, e.g. flagged). The owner is emailed. Commits."""
    review.status, review.reason, review.note, review.decided_by, review.decided_at = "approved", None, None, by, adm.now()
    game = review.game
    if game.hidden == HIDDEN:
        adm.show_game(db, store, game)  # commits
    _tell_owner(db, game.owner, "game_review_approved", {"game": game.name, "url": game_url(game.name)})
    db.commit()


def reject(db: Session, review: GameReview, reason: str, note: str | None, by: str) -> None:
    """Stays unlisted; the owner is emailed why. Commits."""
    review.status, review.reason, review.note = "rejected", reason, (note or "").strip() or None
    review.decided_by, review.decided_at = by, adm.now()
    _tell_owner(db, review.game.owner, "game_review_rejected",
                {"game": review.game_name, "reason": reason, "note": review.note})
    db.commit()


# ---- when games are published and updated (only while the feature is on)


def _on_published(payload: dict) -> None:
    if not flags.is_on(NAME):
        return
    with SessionLocal() as db:
        game = db.get(Game, payload["game"])
        if game is not None and db.get(GameReview, game.name) is None:
            submit(db, game)


def _on_updated(payload: dict) -> None:
    if not flags.is_on(NAME):
        return
    with SessionLocal() as db:
        review = db.get(GameReview, payload["game"])
        if review is not None and review.status == "rejected":
            resubmit(db, review)


events.subscribe("game_published")(_on_published)
events.subscribe("game_updated")(_on_updated)
