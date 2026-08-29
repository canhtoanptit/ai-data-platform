"""Settings, read from the environment.

Every value has a default matching the `postgres` service in the repo's
docker-compose.yml, so `uv run uvicorn app.main:app` works against the local
warehouse with no .env file at all. In Docker, compose overrides POSTGRES_HOST.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Where dbt drops manifest.json / run_results.json / catalog.json.
#
# Resolved from THIS FILE's location, not from the working directory: the
# artifacts live at a fixed spot relative to the repo, while the cwd depends on
# whether you ran `uv run uvicorn` from api/ or `pytest` from somewhere else.
# app/config.py -> app/ -> api/ -> repo root.
#
# In Docker this default is wrong on purpose — the package is installed into
# site-packages, so there is no repo above it. Compose mounts the same directory
# in and sets DBT_ARTIFACTS_DIR to it.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / "anz_banking" / "target"


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

    # Read-only: the catalog/lineage/runs endpoints parse the JSON dbt writes
    # here. See dbt_artifacts.py.
    dbt_artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR

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
