from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/startng"
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 1440
    google_client_id: str = ""
    cors_origins: str = "http://localhost:5173"
    # Games call the API from their own address (<name>.start.ng; <name>.start.localhost while testing)
    game_origins_regex: str = r"^https://[a-z0-9-]+\.start\.ng$|^http://[a-z0-9-]+\.start\.localhost(:\d+)?$"

    # Cloudflare R2 games bucket (S3 API). Games are served by the Worker at <name>.<games_base_domain>
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "startng-games"
    games_base_domain: str = "start.ng"
    # Subdomains that can never be a game name (must match RESERVED_NAMES on the Worker)
    reserved_game_names: str = "api,auth,login,www,3rdmainland"
    # For trying POST /games/publish without the R2 keys: games are written to this folder
    # instead, which the Worker's dev-server.js can serve. Leave empty on the server.
    local_games_dir: str = ""
    # The start.ng website, for links the API hands out (e.g. approving a game published
    # without signing in: <site_url>/approve/<code>).
    site_url: str = "https://start.ng"
    # Game standards (app/checks.py, standards()): a game that doesn't link back to start.ng, show the start.ng name
    # and offer "share on X" is refused when published or updated. Set ENFORCE_GAME_STANDARDS=false to switch off.
    enforce_game_standards: bool = True
    # Where this API is on the internet: page previews for independent games point at their covers here.
    api_url: str = "https://api.start.ng"
    # Keeping games up to date: the server checks each game's GitHub branch for new commits and
    # republishes when it changes (app/auto_update.py). A read-only GitHub token lifts GitHub's
    # limit from 60 checks an hour to 5,000; without one it still works, slowly.
    auto_update: bool = True
    github_token: str = ""
    update_check_seconds: int = 300  # games updated in the last 30 days (newer: every minute; older: hourly)
    # Daily tournaments (app/tournaments.py): each runs 24 hours and ends at tournament_end_hour, local time
    # (UTC + tournament_utc_offset; Nigeria is UTC+1 all year), so the winner is named in the evening.
    tournament_games: int = 5
    tournament_min_games: int = 3  # fewer games than this qualify: no tournament that day
    tournament_end_hour: int = 18
    tournament_utc_offset: int = 1
    # Admin (app/admin.py). Super admins: comma-separated emails, with every permission; they add the other admins.
    # Admins sign in with Google as that email (there's no admin sign-up).
    super_admin_emails: str = ""
    contacts_before_block: int = 3  # issues a developer must have been contacted about before they can be blocked or deleted
    reports_per_day: int = 10  # reports one player can send in 24 hours
    scores_per_minute: int = 30  # scores one player can send to one game in a minute (when the "integrity" feature is on)
    runs_retention_days: int = 180  # how long the Progress Engine keeps each run (daily activity and awards are kept)
    # Feature flags (app/flags.py): comma-separated feature names that are switched on, e.g. "wardrobe,profiles".
    # Every new feature ships dark (off) until its owner says it's ready. "*" switches every feature on (local testing).
    features: str = ""


settings = Settings()
