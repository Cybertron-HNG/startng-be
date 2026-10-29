"""Phone check (Ziki #14): when a game is published or updated, start.ng looks for phone problems and tells its owner
(Your games) and admins. It only warns, never blocks: a rough check that says "worth a look", not "broken".

Always, from the game's files: no viewport tag (the page renders zoomed out), a viewport that isn't width=device-width,
and fixed widths wider than a phone on the page or its canvas. With PHONE_CHECK_BROWSER on (and Playwright's Chromium
installed, see the Dockerfile), it also opens the game at 320 and 375 px and measures what the files can't show:
sideways scrolling and parts of the start screen outside the screen. Rechecking live games on a schedule is #71's."""
from . import service
from .router import router  # noqa: F401

NAME = "phone_check"  # the feature flag
PERMISSIONS: dict[str, str] = {
    "games.phone_check": "See phone checks of games and check them again",
}
TASKS = [service.run_forever]  # the background checker
