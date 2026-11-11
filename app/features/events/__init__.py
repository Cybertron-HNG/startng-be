"""Event spotlight: admins add an event (pictures, description, when, where, a ticket link) and spotlight it. While one is
spotlighted it takes over the top of the homepage and the promotional sections step aside. Only one event is spotlighted
at a time; turning one on turns the other off. The page is /event/<slug>."""
from .router import router  # noqa: F401

NAME = "events"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    "events.manage": "Add events, upload their pictures and spotlight one on the homepage",
}
