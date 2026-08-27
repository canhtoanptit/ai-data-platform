# Collections API

A small **FastAPI** read layer over the marts dbt builds. It is the *consumption
layer*: the warehouse holds the truth, this turns it into JSON a dashboard or an
agent can call. Read-only — nothing here writes to the warehouse.

Interactive docs (Swagger UI) come free at <http://localhost:8000/docs>.

## Endpoints

| Method | Path | Returns | Notes |
|--------|------|---------|-------|
| `GET` | `/api/health` | `{status, database, detail}` | Runs `select 1`. Always 200 — the body says whether the warehouse is reachable. |
| `GET` | `/api/metrics/summary` | One object | Portfolio-wide KPIs: `total_cases`, `open_cases`, `total_delinquent_amount`, `cure_rate_pct`, `ptp_kept_rate_pct`, `rpc_rate_pct`. One aggregate query over `fct_collection_cases`. |
| `GET` | `/api/metrics/performance` | List | Rows of the `collections_performance` mart — the same KPIs split by team × delinquency bucket. |
| `GET` | `/api/cases` | List | `fct_collection_cases`, newest `opened_date` first. |
| `GET` | `/api/cases/{case_id}` | One object | `404` if the case doesn't exist. |

`/api/cases` query params:

| Param | Default | Notes |
|-------|---------|-------|
| `status` | — | `open` \| `resolved` \| `written_off` |
| `bucket` | — | e.g. `1-30 dpd`, `31-60 dpd`, `90+ dpd` |
| `limit` | `50` | 1–200; `201` is a `422`, not a slow query |
| `offset` | `0` | Paging is stable: ordered by `opened_date desc, case_id` |

```bash
curl -s localhost:8000/api/metrics/summary
# {"total_cases":8,"open_cases":4,"total_delinquent_amount":16630.75,
#  "cure_rate_pct":25.0,"ptp_kept_rate_pct":50.0,"rpc_rate_pct":35.3}

curl -s 'localhost:8000/api/cases?status=open&limit=5'
curl -s localhost:8000/api/cases/7001
```

## Running it

The API only serves what dbt has already built, so build the marts first:

```bash
make local-up && make local-build   # from the repo root
```

**Docker (the whole stack):**

```bash
make stack-up      # docker compose up -d --wait  → postgres + api
make stack-down
```

**Local dev (autoreload, Postgres still in Docker):**

```bash
make api-dev       # cd api && uv run uvicorn app.main:app --reload
make api-test      # cd api && uv run pytest
```

`api/` is its **own** uv project with its own `pyproject.toml` and lockfile — it
shares nothing with the dbt tooling in the repo root except the Postgres it
reads from.

## Configuration

[`app/config.py`](./app/config.py) is a `pydantic-settings` model. Every field
has a default matching the `postgres` service in the root `docker-compose.yml`,
so it runs with no `.env` at all.

| Env var | Default | |
|---------|---------|--|
| `POSTGRES_HOST` | `localhost` | compose overrides this to `postgres` |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `platform` | |
| `POSTGRES_USER` | `platform` | |
| `POSTGRES_PASSWORD` | `platform` | |
| `MARTS_SCHEMA` | `analytics_marts` | where dbt's gold layer lands |

The same `POSTGRES_*` names the dbt project uses, on purpose: one set of vars for
the whole stack. Note the host difference — from your laptop the warehouse is
`localhost:5432`, but inside the compose network it is the **service name**,
`postgres:5432`, because `localhost` in a container is that container itself.

## Layout, and one design decision

```
app/
  config.py           settings + the SQLAlchemy URL
  db.py               engine (pool_pre_ping) + the per-request connection dependency
  schemas.py          pydantic response models
  routers/metrics.py  /api/metrics/*
  routers/cases.py    /api/cases/*
  main.py             app factory, CORS, /api/health
tests/test_api.py     integration tests against the real local warehouse
```

**Why parameterised `text()` SQL and no ORM.** The marts *are* dbt's contract —
their columns, grain and KPI formulas are declared and tested over in
`anz_banking/`. Re-declaring them as ORM classes would put that contract in a
second place that can drift silently, and buys nothing: these are read-only
aggregate reads with no relationship graph to traverse. So the SQL names the
columns dbt promises, and every user-supplied value is a bind parameter. The
only string interpolation is the schema name, which comes from config, never
from a request.

The KPI formulas in `/api/metrics/summary` intentionally mirror
`models/marts/collections_performance.sql`, so the portfolio-wide figure and the
per-team breakdown are the same metric at two grains. `tests/test_api.py` asserts
they tie out.

## Tests

`tests/test_api.py` runs FastAPI's `TestClient` against the **real** local
Postgres, because the thing worth testing is that the SQL matches what dbt built
— a mocked database would happily agree with SQL no warehouse accepts. The suite
skips itself (rather than failing) if the warehouse is unreachable.

The expected numbers are the committed seeds in `anz_banking/seeds/` (8 cases).
Change a seed, change the assertions.
