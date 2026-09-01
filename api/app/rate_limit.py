"""Per-IP rate limiting for /api/chat, via slowapi.

**Why only the chat route.** Everything else in this API is a cheap read of a
mart or a cached JSON file — a client hammering `/api/metrics/summary` costs a
few milliseconds of Postgres. `/api/chat` is the only endpoint that spends a
third party's quota and takes seconds, so it is the only one where a limit buys
anything. A global limit would instead throttle a dashboard that legitimately
polls five endpoints on load.

**Why slowapi and not a hand-rolled dict.** slowapi is limits + a bit of
Starlette glue, and it gets the two things a hand-rolled version gets wrong:
correct window arithmetic (a fixed-window counter lets through 2x the limit
across a boundary) and a pluggable storage backend, so the same decorator moves
to Redis behind more than one replica by changing `storage_uri`. The default
in-memory storage is honest about what it is: per-process, reset on restart,
which is the right cost for a single-container demo.

**How this differs from the token budget.** They answer different questions and
both are needed. The rate limit bounds *how fast* one client may ask; the daily
token budget (app/tracing.py) bounds *how much the whole server may spend*. A
single client politely asking one question a minute all day would never trip the
rate limit and would still empty the budget.

The limiter lives in its own module so `main.py` (which registers the handler)
and `routers/chat.py` (which decorates the route) can both import it without an
import cycle.
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# key_func picks the identity being limited. The client IP, because there is no
# authentication here to key on — and `get_remote_address` reads
# `request.client.host`, which behind the nginx proxy in compose is the proxy's
# address. That collapses all browser traffic onto one bucket, which is the wrong
# behaviour in production and a *deliberate* simplification here: trusting
# X-Forwarded-For requires knowing which proxies are yours, and getting that
# wrong lets any client forge its own identity and bypass the limit entirely.
limiter = Limiter(key_func=get_remote_address)


def rate_limit_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Answer 429 in the same `{"detail": ...}` shape as every other error.

    slowapi's built-in handler returns `{"error": "..."}`, which the web client
    (and FastAPI's own convention) does not read. One shape, so the UI has one
    code path for "the server said no".
    """
    limit = getattr(exc, "detail", "the rate limit")
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": (
                f"Too many questions from this address ({limit}). "
                "Wait a moment and ask again."
            )
        },
    )


__all__ = ["RateLimitExceeded", "limiter", "rate_limit_handler"]
