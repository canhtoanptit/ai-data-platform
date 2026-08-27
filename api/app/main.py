"""App factory and the health endpoint.

Serves the dbt-built `analytics_marts` tables as a read-only REST API — the
consumption layer a dashboard or agent would sit on top of.
"""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import engine
from .routers import cases, metrics
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
    app.include_router(health_router)
    app.include_router(metrics.router)
    app.include_router(cases.router)
    return app


# Module-level instance for `uvicorn app.main:app`.
app = create_app()
