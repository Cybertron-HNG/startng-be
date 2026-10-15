"""Keeps live games in step with their GitHub repos.

While the server runs, it checks each game's branch for a new commit and republishes the game
when there is one, so pushing to main is all a builder has to do. Games are checked more often
when they've changed recently:

  updated in the last day      every minute
  updated in the last 30 days  every settings.update_check_seconds (5 minutes)
  older                        every hour

A check asks GitHub only for the branch's latest commit id, sending the fingerprint (ETag) of
the last answer: an unchanged repo gets "304 Not Modified", which costs nothing against GitHub's
limit when a token is set. With GITHUB_TOKEN that limit is 5,000 checks an hour, without it 60.
When GitHub says the limit is used up, checking pauses until it resets.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .config import settings
from .db import SessionLocal
from .game_store import GameStore, get_game_store
from .models import Game
from .publish import PublishError, parse_repo_url

log = logging.getLogger(__name__)

TICK_SECONDS = 30  # how often to look for games that are due
PER_TICK = 50  # at most this many checks per look, so one look never runs long
ACTIVE, RECENT = timedelta(days=1), timedelta(days=30)


@dataclass
class Commit:
    status: str  # "fresh" (a new answer: compare sha), "same" (unchanged), "missing", "limited", "error"
    sha: str | None = None
    etag: str | None = None
    retry_at: datetime | None = None


def latest_commit(repo_url: str, branch: str | None, etag: str | None = None) -> Commit:
    """The latest commit id on a repo's branch (or its main branch), from GitHub."""
    try:
        repo = parse_repo_url(repo_url)
    except PublishError:
        return Commit("missing")
    headers = {"Accept": "application/vnd.github.sha", "User-Agent": "start.ng", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    if etag:
        headers["If-None-Match"] = etag
    url = f"https://api.github.com/repos/{repo.owner}/{repo.name}/commits/{branch or 'HEAD'}"
    try:
        res = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException:
        return Commit("error")
    if res.status_code == 304:
        return Commit("same", etag=etag)
    if res.status_code == 200:
        return Commit("fresh", sha=res.text.strip()[:40], etag=res.headers.get("ETag"))
    if res.status_code in (403, 429) and res.headers.get("x-ratelimit-remaining") == "0":
        reset = res.headers.get("x-ratelimit-reset", "")
        retry_at = datetime.fromtimestamp(int(reset), timezone.utc) if reset.isdigit() else None
        return Commit("limited", retry_at=retry_at or datetime.now(timezone.utc) + timedelta(minutes=15))
    if res.status_code in (404, 409, 422, 451):  # gone, private, empty, or blocked
        return Commit("missing")
    return Commit("error")


def record_commit(game: Game) -> None:
    """After publishing: remember which commit is live (best effort)."""
    if not settings.auto_update:
        return
    found = latest_commit(game.repo_url, game.branch)
    if found.status == "fresh":
        game.last_commit, game.commit_etag = found.sha, found.etag


def _aware(when: datetime | None) -> datetime | None:
    return when.replace(tzinfo=timezone.utc) if when is not None and when.tzinfo is None else when


def check_every(game: Game, now: datetime) -> timedelta:
    age = now - (_aware(game.updated_at) or now)
    if age < ACTIVE:
        return timedelta(minutes=1)
    if age < RECENT:
        return timedelta(seconds=settings.update_check_seconds)
    return timedelta(hours=1)


def is_due(game: Game, now: datetime) -> bool:
    checked = _aware(game.checked_at)
    return checked is None or now - checked >= check_every(game, now)


@dataclass
class Summary:
    checked: int = 0
    updated: list[str] | None = None
    paused_until: datetime | None = None


def check_due_games(make_session: sessionmaker = SessionLocal, now: datetime | None = None, store: GameStore | None = None) -> Summary:
    """Checks the games that are due and republishes the ones whose branch has moved."""
    from .routers.games import _download_and_check, _put_live  # the same steps as publishing

    now = now or datetime.now(timezone.utc)
    summary = Summary(updated=[])
    store = store or get_game_store()
    if store is None:
        return summary
    with make_session() as db:
        games = db.scalars(select(Game)).all()
        due = sorted((g for g in games if is_due(g, now)), key=lambda g: _aware(g.checked_at) or datetime.min.replace(tzinfo=timezone.utc))
        for game in due[:PER_TICK]:
            found = latest_commit(game.repo_url, game.branch, game.commit_etag)
            if found.status == "limited":
                summary.paused_until = found.retry_at
                log.warning("GitHub's limit is used up; checking again at %s. Set GITHUB_TOKEN for more.", found.retry_at)
                break
            summary.checked += 1
            republished = False
            if found.status == "fresh":
                if game.last_commit is None:
                    game.last_commit = found.sha  # first look: this is what's live
                elif found.sha != game.last_commit:
                    try:
                        repo = parse_repo_url(game.repo_url, game.branch)
                        _put_live(db, store, game, game.name, game.owner_id, repo, _download_and_check(repo))
                        summary.updated.append(game.name)
                        republished = True
                        log.info("Updated %s to %s", game.name, found.sha)
                    except (HTTPException, PublishError) as err:
                        # Not retried until the next new commit: a broken commit stays broken.
                        log.warning("Couldn't update %s to %s: %s", game.name, found.sha, getattr(err, "detail", err))
                    game.last_commit = found.sha
                game.commit_etag = found.etag
            elif found.status == "missing":
                log.info("%s: its repo or branch is gone or private; the game stays as it is", game.name)
            if not republished:
                # A check alone isn't an update: keep updated_at, which orders the game list and
                # sets how often the game is checked.
                game.updated_at = Game.updated_at
            game.checked_at = now
            db.commit()
    return summary


async def run_forever() -> None:
    """The background loop started with the server (see main.py)."""
    while True:
        try:
            summary = await run_in_threadpool(check_due_games)
            if summary.updated:
                log.info("Auto-update: updated %s", ", ".join(summary.updated))
            wait = TICK_SECONDS
            if summary.paused_until:
                wait = max(TICK_SECONDS, (summary.paused_until - datetime.now(timezone.utc)).total_seconds())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Auto-update check failed")
            wait = TICK_SECONDS
        await asyncio.sleep(wait)
