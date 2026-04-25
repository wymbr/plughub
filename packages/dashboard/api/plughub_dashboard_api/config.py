"""
Dashboard API configuration.
All settings are read from environment variables with the PLUGHUB_ prefix.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLUGHUB_",
        case_sensitive=False,
    )

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "plughub"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    # Pagination defaults
    default_page_size: int = 50
    max_page_size: int = 200

    # CORS — comma-separated origins
    cors_origins: str = "http://localhost:5174"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
