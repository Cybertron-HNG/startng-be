"""H1 email and notifications: opt-in per topic, double opt-in for visitors, one-click unsubscribe, an outbox with
retries, 1 email per player per day, and notify() for the other features (see service.py)."""
from . import newsletter, service, tournament_mail  # noqa: F401  (they declare their email kinds)
from .router import router  # noqa: F401

NAME = "email"
PERMISSIONS: dict[str, str] = {
    "email.view": "See the email outbox and send a test email",
    **newsletter.PERMISSIONS,
}
TASKS = [service.run_forever]  # the background sender, run while the server is up and the flag is on
