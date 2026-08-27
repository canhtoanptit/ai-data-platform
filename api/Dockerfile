# Two-stage build. The builder resolves and installs dependencies into a venv;
# the final image copies only that venv, so pip, wheels and build caches never
# ship. Result is python:3.12-slim plus the venv.

# --- stage 1: build the virtualenv -------------------------------------------
FROM python:3.12-slim AS builder

# Never write .pyc into the venv we are about to copy; keep pip quiet and cacheless.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
# pyproject + package together: `pip install .` builds the app as a wheel, so
# `app` lands in site-packages exactly as it does for local `uv run`.
# No compiler needed — every dependency (psycopg2-binary included) has a wheel.
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

# --- stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv

# Run unprivileged. WORKDIR is /srv, not /app, so the cwd can never shadow the
# installed `app` package on sys.path.
RUN useradd --create-home --uid 1000 appuser
WORKDIR /srv
USER appuser

EXPOSE 8000

# urllib instead of curl: it is already in the stdlib, so the image stays lean.
# /api/health returns 200 even when the warehouse is down (by design), so this
# check answers "is the API serving?" — which is what `compose up --wait` needs.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"]

# Bind 0.0.0.0, not localhost: a container-local bind is unreachable from the
# host even with a published port.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
