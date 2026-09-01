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
"""

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import engine
from .dbt_artifacts import ArtifactsUnavailable
from .llm import LlmError
from .rate_limit import RateLimitExceeded, limiter, rate_limit_handler
from .routers import agents, cases, catalog, chat, metrics, observability, runs
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


def llm_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Map an LLM failure onto the status code its class already declares.

    One handler for the whole family (see app/llm.py's table): a missing
    GROQ_API_KEY is a 503 with setup instructions, a throttled free tier is a
    429, everything else is a 502. `str(exc)` is safe to return here — every
    message in that module is written by us, never copied from the provider.
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
        # POST is here for exactly one endpoint: /api/chat takes a question in
        # the body. Everything else is still a read.
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ArtifactsUnavailable, artifacts_unavailable_handler)
    app.add_exception_handler(LlmError, llm_error_handler)
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
    return app


# Module-level instance for `uvicorn app.main:app`.
app = create_app()
