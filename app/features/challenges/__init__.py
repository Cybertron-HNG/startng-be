"""B1 challenge links ("Beat my score"): a player turns a run into a link, a friend takes it, and the challenger is
told when it's beaten. The score comes from the run log (progress), the card from share (B2), the email from email (H1)."""
from . import card, service  # noqa: F401  (card registers the "challenge" share kind, service the run_recorded handler)
from .router import router  # noqa: F401

NAME = "challenges"
PERMISSIONS: dict[str, str] = {}
