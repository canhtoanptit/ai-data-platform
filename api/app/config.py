"""Settings, read from the environment.

Every value has a default matching the `postgres` service in the repo's
docker-compose.yml, so `uv run uvicorn app.main:app` works against the local
warehouse with no .env file at all. In Docker, compose overrides POSTGRES_HOST.
"""

import tempfile
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

    # --- AI chat (/api/chat) -------------------------------------------------
    # Optional by design. Empty is the supported state: everything else in the
    # app works without a key, and /api/chat answers 503 with instructions
    # rather than crashing at import time. See app/llm.py.
    groq_api_key: str = ""

    # An OpenAI-*compatible* endpoint, so the provider is one env var away from
    # being swapped (Groq today; OpenAI, Together, a local Ollama tomorrow).
    llm_base_url: str = "https://api.groq.com/openai/v1"

    # Groq lists this as its flagship production model: 128k context, ~280
    # tok/s, and strong enough at SQL for text-to-SQL over four tables. The
    # smaller llama-3.1-8b-instant is faster but noticeably worse at joins;
    # openai/gpt-oss-120b is the other credible pick. Overridable via LLM_MODEL.
    llm_model: str = "llama-3.3-70b-versatile"

    # Groq's free tier is fast but rate limited; 30s is generous for two
    # sequential completions and still bounded so a hung call can't pin a
    # worker thread.
    llm_timeout_seconds: float = 30.0

    # --- Cost + abuse controls on /api/chat ----------------------------------
    # Tokens per UTC day, summed over platform_ops.llm_calls (see app/tracing.py).
    # 200k is roughly a day of demo use at ~2k tokens a question: high enough
    # never to annoy a real user, low enough that a script pointed at the
    # endpoint hits it in minutes rather than exhausting the provider quota.
    llm_daily_token_budget: int = 200_000

    # Requests per client IP, in slowapi's syntax. The budget above bounds *cost*
    # over a day; this bounds *concurrency* right now — one client cannot queue
    # up 200 questions and starve everyone else, and Groq's own free-tier limit
    # is reached long before the token budget is.
    chat_rate_limit: str = "10/minute"

    # --- File-upload ingestion (/api/ingest) ---------------------------------
    # Optional in exactly the same way the LLM key is: with no Airflow running,
    # /api/ingest answers 503 with "make pipeline-up" and nothing else in the
    # app notices. The Airflow service lives behind a compose profile that is
    # off by default, so "not there" is the state the repo ships in.
    #
    # The default is the HOST port (8081, published by docker-compose.yml), which
    # is what `make api-dev` needs. In compose the API reaches Airflow by service
    # name on its container port, so compose sets AIRFLOW_BASE_URL=http://airflow:8080.
    airflow_base_url: str = "http://localhost:8081"

    # HTTP Basic against Airflow's stable REST API. Demo credentials, matching
    # the deterministic admin user docker-compose.yml creates — see the comment
    # there about why they are fixed and why MWAA has no equivalent.
    airflow_username: str = "admin"
    airflow_password: str = "admin"

    # Short on purpose. Every call here is a small control-plane request
    # (trigger a run, read its status) against a service on the same host, so a
    # slow answer means something is wrong; waiting 30s to say so would make the
    # Ingest page feel broken rather than informative.
    airflow_timeout_seconds: float = 5.0

    # Where uploads are staged for Airflow to read. In compose this is a named
    # volume mounted into both containers. The default is a temp directory so the
    # API starts anywhere — but note that a host-run API and a containerised
    # Airflow do NOT share it, so the upload succeeds and the DAG's first task
    # then fails saying it cannot find the file. The full loop wants the whole
    # stack in compose (`make stack-up && make pipeline-up`).
    uploads_dir: Path = Path(tempfile.gettempdir()) / "anz-uploads"

    # 5 MB. Large enough for any hand-made demo extract, small enough that the
    # limit is reached long before memory is: FastAPI spools multipart uploads
    # over 1 MB to a temp file, and the check that enforces this reads in chunks.
    max_upload_bytes: int = 5 * 1024 * 1024

    @property
    def llm_configured(self) -> bool:
        return bool(self.groq_api_key.strip())

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
