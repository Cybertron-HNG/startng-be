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
    # Leagues (app/features/leagues): each track's round length in hours. Rounds line up with tournament_end_hour.
    league_sprint_hours: int = 2
    league_daily_hours: int = 24
    league_weekly_hours: int = 168
    # Admin (app/admin.py). Super admins: comma-separated emails, with every permission; they add the other admins.
    # Admins sign in with Google as that email (there's no admin sign-up).
    super_admin_emails: str = ""
    contacts_before_block: int = 3  # issues a developer must have been contacted about before they can be blocked or deleted
    reports_per_day: int = 10  # reports one player can send in 24 hours
    # Outlier flags (the "integrity" feature): a run is held for review when it beats the player's previous best by more
    # than flag_previous_factor, or the game's 99th percentile by more than flag_percentile_factor.
    flag_previous_factor: int = 3
    flag_percentile_factor: int = 5
    flag_min_previous: int = 20  # a previous best under this is too small to judge a jump by
    flag_min_players: int = 20  # players with a score a game needs before its percentile is used
    scores_per_minute: int = 30  # scores one player can send to one game in a minute (when the "integrity" feature is on)
    runs_retention_days: int = 180  # how long the Progress Engine keeps each run (daily activity and awards are kept)
    # Feature flags (app/flags.py): comma-separated feature names that are switched on, e.g. "wardrobe,profiles".
    # Every new feature ships dark (off) until its owner says it's ready. "*" switches every feature on (local testing).
    features: str = ""
    # Tags (app/features/tags) work on their own: every new, updated and existing game gets the rules' suggestions, and they are
    # approved without a person, except the tags that make a claim about a place or a people (NEEDS_A_PERSON in the tags service),
    # which wait for one. TAGS_AUTO_APPROVE=false puts every approval back with a person.
    tags_auto_approve: bool = True
    # Email (app/features/email, H1). EMAIL_PROVIDER: "log" writes emails to the server log instead of sending
    # (local work and tests); "brevo" sends through Brevo and "smtp" sends through an SMTP server. EMAIL_ONLY_TO:
    # while it's set, mail goes only to these comma-separated addresses and everyone else's is skipped (testing before
    # go-live).
    email_provider: str = "log"
    brevo_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    email_from: str = "start.ng <hello@mail.start.ng>"
    email_reply_to: str = ""  # a monitored inbox for replies; empty: replies go to email_from
    email_only_to: str = ""
    # Brevo calls POST /email/webhooks/brevo?key=<this> about bounces and spam complaints. Empty: webhook off.
    brevo_webhook_key: str = ""
    # Game health (app/features/game_health, H2): besides checking each game's files, open its address and expect an
    # answer. Off when the games' addresses aren't reachable from the API (a laptop without the Worker running).
    game_health_http: bool = True
    email_merge_minutes: int = 10  # an email waits this long, so notes for the same player that day share one email


settings = Settings()
