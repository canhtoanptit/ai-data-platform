"""Settings, read from the environment.

Every value has a default matching the `postgres` service in the repo's
docker-compose.yml, so `uv run uvicorn app.main:app` works against the local
warehouse with no .env file at all. In Docker, compose overrides POSTGRES_HOST.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    # env_prefix is deliberately empty: the dbt project already uses bare
    # POSTGRES_* names, and reusing them means one set of vars for the whole
    # stack. Field names map case-insensitively (postgres_host <- POSTGRES_HOST).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "platform"
    postgres_user: str = "platform"
    postgres_password: str = "platform"

    # Where dbt lands the gold layer: base schema `analytics` + mart `+schema`.
    marts_schema: str = "analytics_marts"

    @property
    def database_url(self) -> URL:
        # URL.create() rather than an f-string: it escapes passwords containing
        # characters that would otherwise break the URL (@, /, :, ...).
        return URL.create(
            drivername="postgresql+psycopg2",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()
