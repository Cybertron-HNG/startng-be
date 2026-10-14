from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/startng"
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 1440
    google_client_id: str = ""
    cors_origins: str = "http://localhost:5173"


settings = Settings()
