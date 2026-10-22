"""Challenge links (PRD B1): "beat my score". A player turns a run into start.ng/c/<code>; friends who take it on have
their runs compared with it, and the challenger is told when it's beaten (challenge_beaten, and an email through H1)."""
from . import service, share  # noqa: F401  (subscribes to run_recorded; registers the email and the share card)
from .router import router  # noqa: F401

NAME = "challenges"  # the feature flag
PERMISSIONS: dict[str, str] = {}
