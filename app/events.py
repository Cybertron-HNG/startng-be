"""In-process events, so features can react to each other without reaching into each other's tables.

    from app import events

    events.publish("game_published", {"game": "lane-dash", "owner_id": 4})   # after the change is committed

    @events.subscribe("game_published")
    def tell_followers(payload: dict) -> None: ...

Fire-and-forget: handlers run straight away, in the order they subscribed, and a handler that fails is logged and
skipped, never breaking the request that published. A handler that needs the database opens its own session
(app.db.SessionLocal), because the publisher's may already be closed. Keep handlers quick; heavy work goes to a
background loop. Every event name and its payload is listed in EVENTS, so a typo fails loudly.
"""
import logging
from collections import defaultdict
from typing import Callable

log = logging.getLogger(__name__)

# Every event, with the payload keys it carries (PRD section 5.2). Add yours here, in one line, when you publish it.
EVENTS: dict[str, tuple[str, ...]] = {
    "game_published": ("game", "owner_id"),  # a new game went live
    "game_updated": ("game", "owner_id", "auto"),  # a new version of a live game went live (auto: from a GitHub push)
    "score_submitted": ("user_id", "game", "stats"),  # a game sent a score (every run, not only personal bests)
    "tournament_finished": ("tournament_id", "winner_id", "players"),  # a daily tournament was settled
    "run_recorded": ("user_id", "game", "run_id"),  # D1
    "award_unlocked": ("user_id", "kind", "key"),  # D1
    "record_beaten": ("game", "user_id", "previous_user_id"),  # D2
    "follow_created": ("follower_id", "target_user_id"),  # S2
    "challenge_beaten": ("challenge_id", "challenger_id", "user_id"),  # B1
    "league_round_finished": ("track", "round_id"),  # M2
    "league_promoted": ("track", "user_id", "tier"),  # M2
    "game_unhealthy": ("game", "reason"),  # H2
}

Handler = Callable[[dict], None]
_handlers: dict[str, list[Handler]] = defaultdict(list)


def _check(name: str) -> None:
    if name not in EVENTS:
        raise KeyError(f'Unknown event "{name}". Add it to EVENTS in app/events.py first.')


def subscribe(name: str) -> Callable[[Handler], Handler]:
    """Decorator: run this function every time `name` is published."""
    _check(name)

    def add(fn: Handler) -> Handler:
        if fn not in _handlers[name]:
            _handlers[name].append(fn)
        return fn
    return add


def unsubscribe(name: str, fn: Handler) -> None:
    if fn in _handlers.get(name, []):
        _handlers[name].remove(fn)


def publish(name: str, payload: dict) -> None:
    """Tell every subscriber. Call it after your change is committed. Never raises because of a subscriber."""
    _check(name)
    missing = [k for k in EVENTS[name] if k not in payload]
    if missing:
        raise KeyError(f'Event "{name}" is missing {", ".join(missing)}')
    for fn in list(_handlers.get(name, [])):
        try:
            fn(payload)
        except Exception:
            log.exception("A %s handler (%s) failed", name, getattr(fn, "__qualname__", fn))
