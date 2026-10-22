"""H2 name filter: keeps offensive words out of names and text people see on start.ng.
Others call service.check_text / service.require_clean (see service.py)."""
from .router import router  # noqa: F401

NAME = "moderation"
PERMISSIONS: dict[str, str] = {
    "moderation.words": "Manage the blocked and allowed words of the name filter",
}
