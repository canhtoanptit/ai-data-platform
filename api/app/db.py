"""Database engine and the FastAPI dependency that hands out connections.

Sync SQLAlchemy Core, not async and not the ORM. The marts are read-only
aggregate tables; there is no session/identity-map work to do, and FastAPI runs
sync endpoints in a threadpool so a blocking driver is fine at this scale.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from .config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    # The warehouse container gets restarted a lot in local dev; pool_pre_ping
    # cheaply validates a pooled connection instead of failing the request with
    # a stale socket.
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

# Schema name for the mart tables. It is a SQL *identifier*, so it can't be a
# bind parameter — it is interpolated into the query strings instead. Safe here
# because it comes from configuration, never from a request.
MARTS = _settings.marts_schema


def get_connection() -> Iterator[Connection]:
    """Yield a connection per request and always return it to the pool."""
    with engine.connect() as connection:
        yield connection


# Saves repeating `Depends(...)` in every signature.
DbConn = Annotated[Connection, Depends(get_connection)]
