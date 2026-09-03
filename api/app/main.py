"""App factory, the health endpoint, and the artifacts-missing handler.

Serves the dbt-built `analytics_marts` tables as a read-only REST API — the
consumption layer a dashboard or agent would sit on top of. Two kinds of
endpoint live behind it: the mart readers (`/api/metrics`, `/api/cases`,
`/api/agents`) query Postgres, and the metadata readers (`/api/catalog`,
`/api/runs`) parse the JSON artifacts dbt writes to `anz_banking/target/`.

`/api/chat` is the third kind and uses both: dbt's metadata becomes the schema
briefing an LLM writes SQL against, and the warehouse runs it. See
`routers/chat.py`.

`/api/observability/llm` is the fourth: it reads the API's *own* operational
table, `platform_ops.llm_calls`, which every chat request writes a row to. See
`routers/observability.py` and `app/tracing.py`.

`/api/ingest` is the fifth and the only *write* path — and it does not write to
the warehouse itself. It validates an uploaded file, stages it in a shared
volume, and triggers an Airflow DAG that does the COPY and a scoped `dbt build`.
See `routers/ingest.py` and `airflow/dags/file_ingest.py`.
"""

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .airflow_client import AirflowApiError
from .db import engine
from .dbt_artifacts import ArtifactsUnavailable
from .llm import LlmError
from .rate_limit import RateLimitExceeded, limiter, rate_limit_handler
from .routers import agents, cases, catalog, chat, ingest, metrics, observability, runs
from .schemas import Health

# Local dev servers for a front end: Vite (5173) and Next/CRA (3000). Kept as an
# explicit list rather than "*" so the allow-list is a visible decision.
ALLOWED_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]

health_router = APIRouter(tags=["health"])


@health_router.get("/api/health", summary="Liveness plus a database round-trip")
def health() -> Health:
    """Always answers 200; the body says whether the warehouse is reachable.

    This uses the engine directly instead of the `DbConn` dependency: a
    dependency that fails to connect raises before the handler body runs, which
    would turn an unreachable database into a 500 rather than a readable report.
    Container health checks therefore see "the app is up", and the payload
    distinguishes "and the warehouse is up too".
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except SQLAlchemyError as exc:
        # Only the exception class, not str(exc) — the driver's message can echo
        # the connection string, password included.
        return Health(status="degraded", database="fail", detail=type(exc).__name__)
    return Health(status="ok", database="ok")


def artifacts_unavailable_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    """Turn "dbt hasn't run yet" into a 503 with instructions.

    503, not 500: the endpoint is fine, its input file just isn't there yet, and
    the fix is one make target away. Registered once here rather than as a
    try/except in every catalog and runs endpoint. The signature takes a bare
    `Exception` because that is what Starlette's handler protocol declares.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


def upstream_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Map a failure in an *optional external service* onto the status its class declares.

    Two families use this, and they have the same shape for the same reason
    (see the tables in app/llm.py and app/airflow_client.py):

    * LLM — a missing GROQ_API_KEY is 503 with setup instructions, a throttled
      free tier is 429, anything else is 502.
    * Airflow — the pipeline profile being off is 503 with `make pipeline-up`,
      a rejected request is 502, an unknown run is 404.

    In both cases the status code is a property of the failure *kind*, so it
    lives on the exception class and there is one handler rather than a
    try/except at every call site. `str(exc)` is safe to return: every message in
    those two modules is written by us, never copied from the upstream service —
    with one deliberate exception, Airflow's own error text, which is noted where
    it is passed through.
    """
    status_code = getattr(exc, "status_code", status.HTTP_502_BAD_GATEWAY)
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


def create_app() -> FastAPI:
    app = FastAPI(
        title="ANZ Collections API",
        version="0.1.0",
        description="Read-only REST layer over the dbt collections marts.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        # POST is here for two endpoints: /api/chat takes a question in the body
        # and /api/ingest takes a multipart file. Everything else is a read.
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ArtifactsUnavailable, artifacts_unavailable_handler)
    app.add_exception_handler(LlmError, upstream_error_handler)
    app.add_exception_handler(AirflowApiError, upstream_error_handler)
    # slowapi reads the limiter off app.state inside its decorator, so this
    # assignment is wiring, not bookkeeping — without it the /api/chat route's
    # @limiter.limit raises at request time. See app/rate_limit.py.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.include_router(health_router)
    app.include_router(metrics.router)
    app.include_router(cases.router)
    app.include_router(agents.router)
    app.include_router(catalog.router)
    app.include_router(runs.router)
    app.include_router(chat.router)
    app.include_router(observability.router)
    app.include_router(ingest.router)
    return app


# Module-level instance for `uvicorn app.main:app`.
app = create_app()
