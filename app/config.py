from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/startng"
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 1440
    google_client_id: str = ""
    cors_origins: str = "http://localhost:5173"

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


settings = Settings()
