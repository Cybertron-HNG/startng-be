"""Score integrity and fast leaderboards (PRD D2): the startng.json `max` per stat, a per-player rate limit, a leaderboard
layer (`service.ranked`, `service.rank_of`) that sorts in the database with a 30 second cache, outlier flags that hold
suspicious runs for review, the review queue, and the `record_beaten` event."""
from . import models, service  # noqa: F401
from .router import router  # noqa: F401

NAME = "integrity"  # the feature flag
PERMISSIONS: dict[str, str] = {
    "scores.review": "Review flagged scores: clear a genuine run or confirm a fake one",
}
