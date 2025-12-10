from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Uses .env file for local development.
    """

    # Database
    DATABASE_URL: str  # TODO: Set via environment variable

    # RuneLite API
    RUNELITE_API_BASE_URL: str = "https://prices.runescape.wiki/api/v1/osrs"

    # Polling Configuration
    POLL_INTERVAL_MINUTES: int = 5

    # Admin API Key
    ADMIN_API_KEY: str  # TODO: Set via environment variable

    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "DEBUG"

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance.
    Only loads .env file once and reuses the same instance.
    """
    return Settings()
