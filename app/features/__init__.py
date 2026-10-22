"""Every feature built on top of start.ng's core lives in its own folder here, owned by one person (see the PRD):

    app/features/<feature>/
        __init__.py   NAME (its flag), router (or None), PERMISSIONS (admin permissions it adds, or {}),
                      and optionally TASKS: background loops (async functions) run while the server is up
        router.py     its API routes
        models.py     its tables (only its own; it never writes another feature's tables)
        schemas.py    its request and response shapes
        service.py    the functions other features may call (its contract)
        rules.py      its award rules, if any (registered with the Progress Engine)
        tests/        its tests

Copy app/features/_template to start one, then add ONE line to FEATURES below. That's the only shared edit:
main.py mounts every feature's router (404 while its flag is off), alembic sees its tables, and the admin page
offers its permissions.
"""
import importlib
from types import ModuleType

# One line per feature, in any order. The name is the folder under app/features.
FEATURES: list[str] = [
    "private_tournaments",
    "follows",
    "moderation",  # H2 name filter (Hamzat)
    "needs_fixing",  # H2 "needs fixing" label (Hamzat)
    "duplicates",  # H2 duplicate finder (Hamzat)
    "email",  # H1 email and notifications (Hamzat)
    "game_health",  # H2 game health checks (Hamzat)
    "progress",
    "integrity",
    "profiles",  # S1 player profiles and badges (Hamzat, for Seyi)
]

_loaded: dict[str, ModuleType] = {}


def load(names: list[str] | None = None) -> list[ModuleType]:
    """Import every feature (its tables, routes, event subscriptions) and register its admin permissions. Safe to
    call more than once."""
    from .. import admin

    out = []
    for name in FEATURES if names is None else names:
        if name not in _loaded:
            module = importlib.import_module(f"{__name__}.{name}")
            for attr in ("NAME", "router", "PERMISSIONS"):
                if not hasattr(module, attr):
                    raise AttributeError(f"app/features/{name}/__init__.py must define {attr} (see _template)")
            admin.register_permissions(module.PERMISSIONS)
            _loaded[name] = module
        out.append(_loaded[name])
    return out
