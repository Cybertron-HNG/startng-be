"""usernames: users.username, unique, with a default for everyone who has an account (app/usernames.py)

Revision ID: 0042
Revises: 0041
"""
import re
import secrets

from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

RESERVED = {"admin", "administrator", "root", "support", "help", "staff", "team", "moderator", "mod", "start", "startng",
            "start_ng", "api", "www", "me", "you", "null", "undefined", "system", "official", "security", "billing",
            "hangout", "games", "tournament", "creators", "docs", "shop", "wardrobe", "profile", "settings", "login",
            "logout", "signin", "signup"}


def _base(email, name, character) -> str:
    raw = (character or {}).get("name") if isinstance(character, dict) else None
    raw = raw or name or (email or "").split("@")[0] or "player"
    base = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:19].strip("_")
    if not re.search(r"[a-z]", base):
        base = f"player_{base}" if base else "player"
    while len(base) < 3:
        base += "x"
    return base


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(24), nullable=True))
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("email", sa.String), sa.column("name", sa.String),
                     sa.column("character", sa.JSON), sa.column("username", sa.String))
    conn = op.get_bind()
    taken: set[str] = set(RESERVED)
    for row in conn.execute(sa.select(users.c.id, users.c.email, users.c.name, users.c.character).order_by(users.c.id)).all():
        base = _base(row.email, row.name, row.character)
        candidate = base
        for attempt in range(60):
            if candidate not in taken:
                break
            width = 3 + attempt // 20
            candidate = f"{base}{secrets.randbelow(10 ** width):0{width}d}"
        else:
            candidate = f"player{secrets.token_hex(6)}"
        taken.add(candidate)
        conn.execute(users.update().where(users.c.id == row.id).values(username=candidate))
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
