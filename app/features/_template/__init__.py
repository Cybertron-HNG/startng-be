"""TEMPLATE: copy this folder to app/features/<your_feature>/, rename things, then add "<your_feature>" to FEATURES
in app/features/__init__.py. Delete what you don't need. (This folder itself is never loaded on the server.)"""
from .router import router  # noqa: F401

NAME = "example"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    # Admin permissions this feature adds, shown when a super admin makes a role.
    "example.manage": "Manage examples",
}
