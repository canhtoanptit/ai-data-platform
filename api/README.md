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
| `GET` | `/api/agents` | List | `dim_agents`, ordered by team. |

And the metadata endpoints, which read dbt's **artifacts** rather than the
warehouse (see below):

| Method | Path | Returns | Notes |
|--------|------|---------|-------|
| `GET` | `/api/catalog/models` | List | Every model, seed and snapshot: layer, schema, materialization, description, column and test counts. |
| `GET` | `/api/catalog/models/{name}` | One object | Adds columns (name, warehouse type, description, tests), `depends_on` / `referenced_by`, and the raw + compiled SQL. `404` if unknown. |
| `GET` | `/api/catalog/lineage` | `{nodes, edges}` | The whole DAG — sources, seeds, models, snapshots. Tests excluded. |
| `GET` | `/api/runs/latest` | One object | Last `dbt build`: `counts` plus a row per executed node. |

And the one endpoint that uses both halves — dbt's metadata as the schema, the
warehouse to run the query (see below):

| Method | Path | Returns | Notes |
|--------|------|---------|-------|
| `POST` | `/api/chat` | One object | `{question}` in, `{question, sql, columns, rows, row_count, truncated, answer, model}` out. `503` without a `GROQ_API_KEY`. |

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

curl -s localhost:8000/api/runs/latest | jq .counts
# {"success":21,"error":0,"skipped":0,"pass":39,"fail":0,"warn":0}
```

## The metadata half: reading dbt's artifacts

`/api/catalog/*` and `/api/runs/*` never touch Postgres. Their source is the
JSON dbt writes into `anz_banking/target/`:

| File | Written by | What is taken from it |
|------|-----------|----------------------|
| `manifest.json` | any dbt command | the DAG, docs, declared columns, tests, raw + compiled SQL |
| `run_results.json` | `dbt build` / `run` / `test` | per-node status and timing |
| `catalog.json` | `dbt docs generate` | the real warehouse column types |

So the catalog is *generated from the pipeline*, not maintained beside it — the
thing that makes a dbt-based catalog worth having.

[`app/dbt_artifacts.py`](./app/dbt_artifacts.py) opens them read-only and caches
the parsed JSON **keyed on file mtime**: manifest.json is ~900 KB, so re-reading
it per request would be wasteful, but caching forever would mean a
`make local-build` in another terminal has no effect until the API restarts.

A missing file raises `ArtifactsUnavailable`, which `main.py` turns into a
**503** carrying the fix (`run make local-build && make local-docs`) — a
temporary condition, not a server error, and the dashboard renders it as an
empty state rather than a crash.

Four things about dbt's artifacts that the code has to know:

- **Ephemeral models are in the manifest but not in run_results.** dbt compiles
  them into their consumers as CTEs instead of executing them, so this project's
  63 nodes produce 60 results. They *are* in the catalog and the lineage graph —
  they are real nodes with real edges.
- **Manifest columns ≠ warehouse columns.** The manifest lists only columns
  documented in a `.yml` (3 of `fct_collection_cases`' 18); catalog.json lists
  all 18 with types but no descriptions. The detail endpoint unions them,
  matching case-insensitively because Postgres reports lower-case column names
  and Snowflake upper-case.
- **Tests are attributed by `attached_node`, not by `depends_on`.** A
  `relationships` test depends on two models — the child it is declared on and
  the parent it points at — so `depends_on` would count it twice. The test suite
  asserts the totals: 39 tests, 39 attributions.
- **`dbt docs generate` clobbers run_results.json.** It runs a compile pass
  first and overwrites the file with compile results — every node "success",
  no test pass/fail left. `make local-docs` passes `--no-compile` for exactly
  this reason.

## Ask AI: `POST /api/chat`

Natural language in, SQL + rows + a sentence out.

```bash
curl -s localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"question":"Which team has the highest cure rate?"}' | jq
# {"question":"Which team has the highest cure rate?",
#  "sql":"SELECT team, ...\nFROM analytics_marts.collections_performance\nLIMIT 100",
#  "columns":["team","cure_rate_pct"], "rows":[["early_stage",100.0]],
#  "row_count":1, "truncated":false,
#  "answer":"The early_stage team has the highest cure rate at 100%.",
#  "model":"llama-3.3-70b-versatile"}
```

Two LLM calls with the warehouse in between:

```
question ──► LLM #1: write SQL (given the schema briefing)
                │
                ▼
             sql_guard.validate ── rejected ──► retry once with the error,
                │                                then 422 with the attempt
                ▼
             read-only transaction, 5s statement timeout, ≤100 rows
                │
                ▼
             LLM #2: summarise the rows ── fails ──► answer: null, rows stand
```

**The schema the model sees is generated by dbt.**
[`app/schema_context.py`](./app/schema_context.py) builds the briefing from
`manifest.json` (model + column descriptions) and `catalog.json` (real warehouse
types), reusing the *same* column-union function the catalog endpoint uses
(`merge_columns`) so the two can never disagree. Add a column to a mart, rebuild,
and the model knows about it on the next request — nothing to maintain by hand.
It is cached against the artifacts' mtimes, like everything else derived from
them. Marts only (four tables), and no row data ever enters the prompt.

**Untrusted SQL, three independent layers.** An LLM is not adversarial but it is
steerable, and the question arrives over HTTP. So none of these relies on the
others being right:

1. [`app/sql_guard.py`](./app/sql_guard.py) — **sqlglot parses** the statement
   and refuses anything that is not a single SELECT. A parser rather than a
   regex, because every text-level allow-list has the same hole: it reasons
   about spelling while the database reasons about a parse tree
   (`select 1; drop table x`, a `--` comment hiding a statement, `delete` inside
   a string literal). It also walks the whole tree, so a data-modifying CTE —
   `with x as (delete from t returning *) select * from x`, which Postgres
   really does allow — is caught, and it enforces the `LIMIT`: missing → added,
   too large → clamped. What executes is *re-generated from the parse tree*, so
   anything the parser silently dropped cannot come along for the ride.
2. **`SET TRANSACTION READ ONLY`** around the execution. If the guard were wrong,
   Postgres itself refuses the write.
3. **`SET LOCAL statement_timeout = '5s'`**. A query can be perfectly read-only
   and still be a denial of service (a cross join over the fact table); the
   timeout bounds what a *valid* query can cost, which neither other layer can.
   `SET LOCAL` so it cannot leak onto the next request using that pooled
   connection.

`pg_*` and `information_schema` are blocked too — defence in depth rather than a
confidentiality boundary, since the connection only reads marts, but a question
reaching for the system catalogs is not a question about collections data.

**Failure modes, and why each code.** All of them are typed exceptions in
[`app/llm.py`](./app/llm.py) with a single handler in `main.py`:

| Situation | Code | Body |
|-----------|------|------|
| no `GROQ_API_KEY` | `503` | how to get a free key and where to put it |
| Groq free tier throttled | `429` | "retry shortly" — genuinely retryable |
| provider timed out / 5xx | `502` | a class name, never the provider's text (it can echo the request) |
| model's SQL rejected twice, or the warehouse refused it | `422` | `{message, sql, error}` — the attempt, so the UI can show what it tried |
| summarising call failed | `200` | `answer: null`, everything else intact |

That last row is the interesting trade: the rows and the SQL *are* the answer, so
losing the sentence describing them must not turn a successful query into an
error page.

**Provider choice is one env var.** Groq serves an OpenAI-*compatible* API, so
the official `openai` SDK talks to it with nothing changed but `base_url`. Point
`LLM_BASE_URL` at OpenAI, Together or a local Ollama and no code changes.
`LLM_MODEL` defaults to `llama-3.3-70b-versatile`, Groq's flagship production
model (128k context, ~280 tok/s, free tier).

**Single-turn, on purpose.** No conversation history is sent or stored, so
follow-ups ("and by team?") don't work. Memory turns a stateless endpoint into a
session store; the interesting problem here is the SQL.

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
| `DBT_ARTIFACTS_DIR` | `../anz_banking/target` | resolved from the *file's* location, not the cwd; compose overrides it to `/srv/dbt-target` |
| `GROQ_API_KEY` | *(empty)* | AI chat. Empty is a supported state: `/api/chat` answers 503, everything else works. Free key at <https://console.groq.com> |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | Groq's flagship production model |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | any OpenAI-compatible endpoint |
| `LLM_TIMEOUT_SECONDS` | `30` | per LLM call; `max_retries=0`, so one timeout is one timeout |

`DBT_ARTIFACTS_DIR`'s default is deliberately wrong inside Docker: the app is
installed into site-packages, where there is no repo above it. Compose
bind-mounts the same directory in **read-only** — the artifacts are build
outputs owned by whoever ran `make local-build`, and the API only ever reads
them.

The same `POSTGRES_*` names the dbt project uses, on purpose: one set of vars for
the whole stack. Note the host difference — from your laptop the warehouse is
`localhost:5432`, but inside the compose network it is the **service name**,
`postgres:5432`, because `localhost` in a container is that container itself.

## Layout, and one design decision

```
app/
  config.py            settings + the SQLAlchemy URL + the artifacts path
  db.py                engine (pool_pre_ping) + the per-request connection dependency
  dbt_artifacts.py     mtime-cached loader for manifest / run_results / catalog
  schemas.py           pydantic response models for the mart rows
  schemas_catalog.py   pydantic response models for the dbt metadata
  routers/metrics.py   /api/metrics/*
  routers/cases.py     /api/cases/*
  routers/agents.py    /api/agents
  routers/catalog.py   /api/catalog/*   (manifest + catalog.json)
  routers/runs.py      /api/runs/latest (run_results.json)
  schemas_chat.py      pydantic request/response models for /api/chat
  schema_context.py    the dbt-generated schema briefing the LLM writes SQL against
  sql_guard.py         sqlglot validation: one SELECT, no writes, a LIMIT
  llm.py               chat-completions wrapper + the typed failure taxonomy
  routers/chat.py      /api/chat    (LLM -> guard -> read-only query -> LLM)
  main.py              app factory, CORS, /api/health, the 503 + LLM handlers
tests/test_api.py           integration tests against the real local warehouse
tests/test_catalog.py       integration tests against the real dbt artifacts
tests/test_sql_guard.py     unit tests for the guard — no warehouse, no key, never skipped
tests/test_schema_context.py the briefing, built from the real artifacts
tests/test_chat.py          the unconfigured 503 + request validation; live path needs a key
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

`tests/test_catalog.py` is the same bargain one level up: it runs against the
**real** artifacts, not a committed fixture manifest, because a fixture would
drift from the dbt project the moment a model moved folders — and agreeing with
the real project is the entire job of those endpoints. It skips itself if the
artifacts are missing.

The expected numbers are the committed dbt project: 8 cases in
`anz_banking/seeds/`, 24 catalogued nodes, 39 tests, 60 run results. Change a
seed or add a model, change the assertions.

`tests/test_sql_guard.py` is the exception to the no-mocks rule, and the most
valuable file in the suite: the guard is pure functions over strings, so it has
no warehouse, no artifacts and no API key to depend on, and it is **never
skipped**. It asserts both directions — that joins, CTEs, subqueries, unions,
aggregates and Postgres-specific syntax (`filter (where ...)`, `::` casts) are
allowed, and that writes, DDL, `GRANT`, `COPY`, `SET`, `COMMIT`, multi-statement
payloads, data-modifying CTEs, `pg_*` / `information_schema` reads, `pg_sleep()`
and unparseable prose are not — plus the LIMIT arithmetic (added, clamped, an
inner subquery's LIMIT not mistaken for the outer one).

`tests/test_chat.py` splits by what each part needs. The unconfigured 503 and
request validation need nothing at all — they are the contract the dashboard
relies on before anyone signs up for a key, so they always run. The happy path
and a prompt-injection case (`"...DROP TABLE dim_agents..."`, asserting the
table is still there) need a real key and skip themselves without one. There is
deliberately no mocked-LLM happy path: a mock would only prove that our code
passes our own fake SQL through, which the guard tests already cover far better.
The thing worth testing live is the one thing a mock cannot check — that a real
model, given this briefing, writes SQL this warehouse accepts.
