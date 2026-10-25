import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    """Every test sees the same settings, whatever the local .env says, and never calls GitHub."""
    monkeypatch.setattr(settings, "auto_update", False)  # publishing doesn't look up the live commit
    monkeypatch.setattr(settings, "site_url", "https://start.ng")
    monkeypatch.setattr(settings, "games_base_domain", "start.ng")
    monkeypatch.setattr(settings, "github_token", "")
    # The game standards have their own tests (test_standards.py); the rest use small games that skip them.
    monkeypatch.setattr(settings, "enforce_game_standards", False)
    monkeypatch.setattr(settings, "email_provider", "log")  # never sends real email
    monkeypatch.setattr(settings, "email_only_to", "")
    monkeypatch.setattr(settings, "feature_switches", False)  # admin switches come from a database; tests that want them turn this on
