from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).parent.parent


def test_migrations_have_one_head():
    """Two migrations that both build on the same one leave `alembic upgrade head` with nothing to
    pick, so the container fails on start and the API goes down. Merging two branches that each
    add a migration can do this without a git conflict."""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1, f"alembic has more than one head: {script.get_heads()}"
