"""Score integrity and fast leaderboards (PRD D2). This drop: the startng.json `max` per stat, a per-player rate limit,
and a leaderboard layer (`service.ranked`, `service.rank_of`) that sorts in the database, with a 30 second cache.
Flagged outliers, the flag queue and the admin tab come next."""
from . import service  # noqa: F401

NAME = "integrity"  # the feature flag
router = None  # no routes yet
PERMISSIONS: dict[str, str] = {}
