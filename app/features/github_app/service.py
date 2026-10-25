"""F1: the start.ng GitHub App. Owners install it on their game's repo; GitHub then tells start.ng about every push
(a signed webhook), and the game updates straight away instead of at the next check (app/auto_update.py, which stays
as the fallback for repos without the app).
"""
import hashlib
import hmac
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import auto_update
from ...config import settings
from ...db import SessionLocal
from ...game_store import get_game_store
from ...models import Game

log = logging.getLogger(__name__)

NAME = "github_app"
NO_COMMIT = "0" * 40  # a push that deleted the branch


def signature_ok(body: bytes, header: str | None) -> bool:
    """GitHub signs the raw body with the webhook secret: X-Hub-Signature-256: sha256=<hex>."""
    if not settings.github_webhook_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def games_for_push(db: Session, payload: dict) -> list[tuple[str, str]]:
    """The games this push should update: [(game name, commit)]. A repo is one game, on the branch it was published
    from (or the repo's default branch). Nothing for a deleted branch, or a commit that's already live."""
    sha = payload.get("after") or ""
    ref = payload.get("ref") or ""
    repo = payload.get("repository") or {}
    url = (repo.get("html_url") or "").rstrip("/").lower()
    if payload.get("deleted") or sha == NO_COMMIT or not ref.startswith("refs/heads/") or not url:
        return []
    branch = ref.removeprefix("refs/heads/")
    out = []
    for game in db.scalars(select(Game).where(func.lower(Game.repo_url) == url)).all():
        if (game.branch or repo.get("default_branch")) == branch and game.last_commit != sha:
            out.append((game.name, sha))
    return out


def update(name: str, sha: str) -> str:
    """Republish one game (in the background, after the webhook has answered GitHub). Returns the outcome."""
    store = get_game_store()
    if store is None:
        return "retry"
    with SessionLocal() as db:
        game = db.get(Game, name)
        if game is None or game.last_commit == sha:
            return "same"
        now = datetime.now(timezone.utc)
        outcome = auto_update.update_to(db, store, game, sha, now)
        if outcome != "retry":
            game.checked_at = now
        if outcome != "updated":
            game.updated_at = Game.updated_at  # a failed try isn't an update (it orders the game list)
        db.commit()
        log.info("GitHub push: %s -> %s (%s)", name, sha[:7], outcome)
        return outcome
