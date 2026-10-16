"""seed the Hangout

The Hangout is start.ng's own game. It runs inside the start.ng front end at /hangout, but its live
room (POST /games/hangout/ticket, WS /games/hangout/live) needs a `hangout` row in `games`, and a row
needs an owner. So it belongs to a start.ng system account that nobody can sign in to: no password,
no Google link, and an address that can never be a real one (.invalid), so a Google account can't be
matched to it either.

If someone has already published a game called `hangout`, it is left alone.

Revision ID: 0007
Revises: 0006
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

GAME = "hangout"
OWNER_EMAIL = "system@start.invalid"
REPO_URL = "https://github.com/Blackburn0/startng-hangout"

users = sa.table(
    "users",
    sa.column("id", sa.Integer),
    sa.column("email", sa.String),
    sa.column("name", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
games = sa.table(
    "games",
    sa.column("name", sa.String),
    sa.column("owner_id", sa.Integer),
    sa.column("repo_url", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    db = op.get_bind()
    if db.scalar(sa.select(games.c.name).where(games.c.name == GAME)) is not None:
        return
    now = datetime.now(timezone.utc)
    owner_id = db.scalar(sa.select(users.c.id).where(users.c.email == OWNER_EMAIL))
    if owner_id is None:
        db.execute(sa.insert(users).values(email=OWNER_EMAIL, name="start.ng", created_at=now))
        owner_id = db.scalar(sa.select(users.c.id).where(users.c.email == OWNER_EMAIL))
    db.execute(sa.insert(games).values(name=GAME, owner_id=owner_id, repo_url=REPO_URL, created_at=now, updated_at=now))


def downgrade() -> None:
    db = op.get_bind()
    owner_id = db.scalar(sa.select(users.c.id).where(users.c.email == OWNER_EMAIL))
    if owner_id is None:
        return
    # Only the seeded game: a `hangout` that a real person published stays.
    db.execute(sa.delete(games).where(games.c.name == GAME, games.c.owner_id == owner_id))
    if db.scalar(sa.select(sa.func.count()).select_from(games).where(games.c.owner_id == owner_id)) == 0:
        db.execute(sa.delete(users).where(users.c.id == owner_id))
