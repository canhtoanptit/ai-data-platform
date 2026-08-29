"""App factory, the health endpoint, and the artifacts-missing handler.

Serves the dbt-built `analytics_marts` tables as a read-only REST API — the
consumption layer a dashboard or agent would sit on top of. Two kinds of
endpoint live behind it: the mart readers (`/api/metrics`, `/api/cases`,
`/api/agents`) query Postgres, and the metadata readers (`/api/catalog`,
`/api/runs`) parse the JSON artifacts dbt writes to `anz_banking/target/`.
"""

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import engine
from .dbt_artifacts import ArtifactsUnavailable
from .routers import agents, cases, catalog, metrics, runs
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
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ArtifactsUnavailable, artifacts_unavailable_handler)
    app.include_router(health_router)
    app.include_router(metrics.router)
    app.include_router(cases.router)
    app.include_router(agents.router)
    app.include_router(catalog.router)
    app.include_router(runs.router)
    return app


# Module-level instance for `uvicorn app.main:app`.
app = create_app()
