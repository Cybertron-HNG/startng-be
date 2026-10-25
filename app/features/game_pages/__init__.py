"""Pages about each game (PRD workstream 2, Chloe): about, how to play, FAQ and walkthrough, written by the server so search
engines and people see the same page. Each is made from facts in the game's own files (startng.json, README, its page),
never invented, and approved by themselves when they pass a safety check (an admin can edit or take any page down). A game that doesn't say enough gets no page and goes on a
"needs a description" list instead."""
from ... import events
from . import service  # noqa: F401
from .router import router  # noqa: F401

NAME = "game_pages"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    "gamepages.manage": "Make, read, edit and approve the about, how to play, FAQ and walkthrough pages of games",
}

# Drafts are made on their own: for every game that has none (a background job) and again when a game gets a new version.
# Approval is automatic too (service.is_safe); GAME_PAGES_AUTO_APPROVE=false puts it back with a person.
TASKS = [service.run_forever]
events.subscribe("game_updated")(service.on_game_updated)
