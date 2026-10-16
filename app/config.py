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
    # Keeping games up to date: the server checks each game's GitHub branch for new commits and
    # republishes when it changes (app/auto_update.py). A read-only GitHub token lifts GitHub's
    # limit from 60 checks an hour to 5,000; without one it still works, slowly.
    auto_update: bool = True
    github_token: str = ""
    update_check_seconds: int = 300  # games updated in the last 30 days (newer: every minute; older: hourly)

    # Explore games: games other people host on their own sites, listed on start.ng (app/explore.py).
    # Anyone can submit one without signing in, so these keep the list clean.
    explore_auto_approve: bool = True  # new games show at once; False = hidden until a moderator approves
    explore_per_hour: int = 5  # games one address can list an hour
    explore_total_per_hour: int = 60  # games anyone can list an hour, in total
    explore_max_listed: int = 500  # the most games the list returns
    # Lets a moderator remove or approve listings (header X-Admin-Token). Empty = there are no moderators.
    explore_admin_token: str = ""


settings = Settings()
