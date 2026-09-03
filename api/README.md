# Collections API

A small **FastAPI** read layer over the marts dbt builds. It is the *consumption
layer*: the warehouse holds the truth, this turns it into JSON a dashboard or an
agent can call. Nothing here writes to the warehouse — including `/api/ingest`,
which validates an uploaded file and hands it to Airflow rather than loading it
itself.

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
| `POST` | `/api/chat` | One object | `{question}` in, `{question, sql, columns, rows, row_count, truncated, answer, model}` out. `503` without a `GROQ_API_KEY`. Rate limited per IP and capped by a daily token budget. |
| `GET` | `/api/observability/llm` | `{today, recent}` | Today's LLM spend against the budget plus the last 20 traced calls. Always `200` — zeros when nothing has been asked. |

And the ingestion endpoints, which talk to **Airflow** rather than to the
warehouse or the artifacts (see below):

| Method | Path | Returns | Notes |
|--------|------|---------|-------|
| `GET` | `/api/ingest/tables` | List | The allow-listed raw tables, each with the header a file must have and the dbt model built downstream. A static list — always `200`, even with Airflow off. |
| `POST` | `/api/ingest` | `202 {dag_run_id, poll, …}` | Multipart: `table` + `file` (+ optional `delimiter`). `422` unknown table / bad extension / header mismatch, `413` over 5 MB, `503` when Airflow is not running. |
| `GET` | `/api/ingest/runs/{dag_run_id}` | One object | Normalised run state plus a row per DAG task. Poll while `is_running`. |

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
question ──► rate limit (per IP) + token budget (per day)  ── over ──► 429
                │
                ▼
             LLM #1: write SQL (given the schema briefing)
                │
                ▼
             sql_guard.validate ── rejected ──► retry once with the error,
                │                                then 422 with the attempt
                ▼
             read-only transaction, 5s statement timeout, ≤100 rows
                │
                ▼
             LLM #2: summarise the rows ── fails ──► answer: null, rows stand
                │
                ▼
             one row into platform_ops.llm_calls (best effort, always)
```

**The pipeline is a module, not a handler.** Prompts, the LLM calls, the guard
and the execution live in [`app/nl2sql.py`](./app/nl2sql.py); `routers/chat.py`
holds only HTTP concerns (status codes, the budget gate, the trace row, the
response model). That split exists so the eval harness runs the *identical* code
path — a runner with its own prompt or its own retry rule measures a system
nobody ships. `nl2sql.PROMPT_VERSION` is the version stamp for both: bump it when
a prompt changes, because evals and traces are only comparable within a version.

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
| more than `CHAT_RATE_LIMIT` from one IP | `429` | "wait a moment" — clears within the minute |
| `LLM_DAILY_TOKEN_BUDGET` spent | `429` | the figures used/allowed, and that it resets at midnight UTC |
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

## Evals: is the SQL any good?

"It worked when I tried it" is not a measurement. [`evals/`](./evals) is a small
harness that scores the NL→SQL feature against a golden set of questions, through
the same `app/nl2sql.py` the endpoint uses.

```bash
make eval                       # from the repo root
make eval ARGS="--threshold 75" # for CI: exit 1 below 75% accuracy
```

**Two modes, and it says which, loudly.**

| | With `GROQ_API_KEY` | Without |
|--|--|--|
| mode | `live` | `reference-check` |
| LLM writes SQL | yes | **skipped** |
| `reference_sql` validated + executed | yes | yes |
| reports accuracy | yes | no — it says so in a banner |

Reference-check mode is not a degraded no-op. A broken golden file is the failure
that makes every future eval meaningless, and this catches it on a laptop with no
key and in CI with no secret. The run **exits 1 if any reference SQL fails, in
both modes** — so CI cannot silently pass a golden file whose SQL no longer runs
— and the summary refuses to print an accuracy figure it did not measure.

```
==============================================================================
  REFERENCE-CHECK mode — no GROQ_API_KEY was set, so
  *** THE LLM LEG WAS SKIPPED ***
  Only the golden file's reference SQL ran. Nothing below says
  anything about model accuracy.
==============================================================================
  questions            4
  reference SQL ok     4/4
  valid-SQL rate       –
  execution success    –
  execution accuracy   –
  total tokens         0
  wall time            0.08s
  prompt version       2026-08-31.1
  report               evals/results/2026-08-31T14-29-34Z.json
```

**Four metrics, because they have four different fixes.** Per question the runner
reports `valid_sql` (the guard accepted it → a prompt problem), `executed` (the
warehouse accepted it → a schema problem), `results_match` (it answered the
question → a reasoning problem), plus latency and tokens (a config problem).
Execution accuracy divides by *every* question, not just the ones that executed:
a question the model could not write SQL for is a question it got wrong.

**Scoring compares result sets, never SQL text**
([`evals/compare.py`](./evals/compare.py)). There are a dozen correct ways to
write "total by bucket", so string similarity would measure our taste rather than
the model's accuracy. The rules, each of which is a judgement:

- **row order ignored** — unordered *multiset* comparison. Multisets, not sets:
  duplicate rows are data, and collapsing them would hide a wrong `group by`.
- **column names ignored, column count enforced** — an alias is the model's
  choice (`total_amount` vs `sum` is the same answer); arity is not, so three
  columns where two were asked for is a different query.
- **Decimal ≡ float** (tolerance `1e-9`), **dates ≡ their ISO strings**, and
  **NULL only equals NULL** — every rate in these marts is null rather than 0
  when its denominator is empty, and a comparison that conflated them would score
  a missing `nullif` as correct.

`tests/test_eval_compare.py` pins all of it and never skips — no warehouse, no
key, pure functions over rows.

**Extending `golden.yaml` is the intended exercise.** Four questions tell you
almost nothing; the questions *you* care about are the ones worth measuring. The
file's header comment explains the format and suggests what to add next (a
cross-mart join, the ordinal `delinquency_bucket` sort, a question whose honest
answer is zero rows, and one you expect the model to get *wrong* — a suite that
always scores 100% has stopped measuring anything).

Reports land in `evals/results/<timestamp>.json`, gitignored: one
self-contained document per run, which is the shape that makes `diff` and `jq`
useful when comparing prompt A against prompt B. Eval calls are traced with
`source='eval'`, so they show up in the observability endpoint but stay separable
from live traffic — and their tokens *do* count against the daily budget, because
they are real tokens on the same key.

## Observability: `platform_ops.llm_calls`

Every `/api/chat` request that gets past the is-configured check writes one row
to a Postgres table the API owns ([`app/tracing.py`](./app/tracing.py)):

```sql
create table if not exists platform_ops.llm_calls (
    id                serial primary key,
    ts                timestamptz  not null default now(),
    source            text         not null default 'chat',  -- 'chat' | 'eval'
    question          text         not null,
    prompt_version    text         not null,
    model             text         not null,
    tokens_prompt     int,
    tokens_completion int,
    latency_ms_total  int          not null,
    latency_ms_llm    int,
    latency_ms_sql    int,
    sql_text          text,
    guard_ok          boolean,
    guard_error       text,
    row_count         int,
    answered          boolean      not null,
    error_class       text,
    http_status       int          not null
);
```

**Why `platform_ops` and not a dbt schema.** This is operational data owned by
the API — the API creates it, the API writes it, dbt must never see it. In
`analytics_*` it would look like a mart: something with a declared grain and
tests, that `dbt build` may drop and rebuild. A separate schema puts the
ownership boundary in the object name. The schema and table are created on first
use with `CREATE ... IF NOT EXISTS` rather than by a migration tool; for one
operational table owned by one service, Alembic is more machinery than the thing
it manages, and the trade (no versioned migration path) costs only history if the
table is dropped.

**Tracing is best-effort.** Every write is wrapped in `except-log-continue`: a
failed trace must never fail the request it describes. Observability that can
take down the thing it observes is a liability, not a safety net.

**One path is deliberately untraced**: a request that 503s for a missing
`GROQ_API_KEY`. There is no call to record — no model, no tokens, no SQL — and a
row saying "the server is unconfigured" is a fact about deployment rather than an
event in the LLM's history. It would also pollute the failure rate the panel
reports. `tests/test_observability.py` asserts no row appears.

**`prompt_version`** (`nl2sql.PROMPT_VERSION`) is on every row. Bump it when the
prompt changes: an accuracy of 3/4 last week and 4/4 today mean nothing side by
side if the prompt moved in between, and this column is what lets you notice
instead of celebrating.

```bash
curl -s localhost:8000/api/observability/llm | jq
# {"today": {"calls": 3, "tokens": 4120, "budget": 200000, "budget_used_pct": 2.1},
#  "recent": [{"ts": "2026-08-31T09:15:04.120000+00:00",
#              "question": "Which team has the highest cure rate?",
#              "source": "chat", "model": "llama-3.3-70b-versatile",
#              "guard_ok": true, "row_count": 1, "tokens": 1402,
#              "latency_ms_total": 1840, "http_status": 200,
#              "error_class": null}]}
```

Always `200` — an empty table, a table that does not exist yet, an unreachable
database all answer with zeros and an empty list. This is what a dashboard polls,
and a panel that goes red because *nothing has happened yet* trains people to
ignore it.

### Budget and rate limit

Two controls answering two different questions, both needed:

| | Bounds | Scope | Enforced by |
|--|--|--|--|
| `LLM_DAILY_TOKEN_BUDGET` | how much the **server** may spend per UTC day | global | one `sum()` over `llm_calls` |
| `CHAT_RATE_LIMIT` | how fast **one client** may ask | per IP | slowapi, in-memory |

A single client politely asking one question a minute all day would never trip
the rate limit and would still empty the budget.

**The budget is just a SQL query over the trace table** — no counter, no Redis,
no in-process state to lose on a restart, and it stays correct across replicas
because Postgres is where they all agree. Observability and control from one
artifact: the number the dashboard shows you is the number that stops you. Past
the budget, `/api/chat` answers `429` naming the figures and the reset time, and
**that rejection is itself traced** (`error_class='BudgetExhausted'`, no tokens) —
throttling that leaves no trace is indistinguishable from a quiet day.

The rate limit is on the chat route only. Everything else here is a cheap mart
read or a cached file; `/api/chat` is the one endpoint that spends a third
party's quota and takes seconds. It answers in the same `{"detail": ...}` shape as
every other error rather than slowapi's `{"error": ...}`, so the web client needs
one code path. Note that behind the nginx proxy in compose, `request.client.host`
is the *proxy's* address, so all browser traffic shares one bucket — a deliberate
simplification, because trusting `X-Forwarded-For` requires knowing which proxies
are yours, and getting that wrong lets any client forge its identity and bypass
the limit entirely.

## Ingestion: `POST /api/ingest`

The only write path, and it writes a file, not a row. The API's job here is to be
a **validating front door**; Airflow's `file_ingest` DAG is the execution
authority.

```bash
curl -sS -X POST localhost:8000/api/ingest \
  -F table=raw_payments -F file=@../web/public/samples/new_payments.csv
# 202
# {"dag_run_id":"ingest__20260903T050541__f69d0cfb","dag_id":"file_ingest",
#  "table":"raw_payments","filename":"ingest__20260903T050541__f69d0cfb__new_payments.csv",
#  "poll":"/api/ingest/runs/ingest__20260903T050541__f69d0cfb"}

curl -s localhost:8000/api/ingest/runs/ingest__20260903T050541__f69d0cfb
# {"state":"success","is_running":false, "tasks":[
#   {"task_id":"copy_into_raw","state":"success","duration_seconds":0.138,…},
#   {"task_id":"dbt_build_downstream","state":"success","duration_seconds":3.687,…}]}
```

Five checks run before anything is staged, in this order: the table is on the
allow-list, the extension is one we parse (`.csv` → comma, `.psv`/`.txt` → pipe,
overridable), the body is under 5 MB, it is not empty, and its header matches the
table's columns (order- and case-insensitive — the DAG names the columns in its
`COPY` in the file's own order, and Postgres folded them to lowercase). Only then
is the file written to `UPLOADS_DIR` and a DAG run triggered.

Four decisions worth naming:

* **The run id is ours, not Airflow's.** Left alone Airflow names a manual run
  `manual__2026-09-03T05:05:41.823037+00:00`; `+` and `:` in a URL path segment
  work until some proxy decides otherwise. `ingest__<ts>__<8 hex>` is
  `[A-Za-z0-9_]` only — and it prefixes the staged filename too, so a file in the
  volume is traceable to the run that consumed it.
* **Only the path travels in `conf`.** A 5 MB CSV must never end up in Airflow's
  metadata database. The API and Airflow share a named docker volume.
* **The client filename is data.** It arrives in a multipart header anyone can
  write by hand and is the one caller-supplied string this API joins to a
  filesystem path, so it is reduced to an allow-list of characters after taking
  the last segment under either separator. `tests/test_ingest.py` asserts the
  property directly: whatever comes in, the joined path stays in its directory.
* **The allow-list is duplicated in the DAG on purpose.** `INGESTABLE` here
  exists so the browser gets a fast, specific 422; `TABLE_TO_STAGING` there
  exists because the DAG is what actually writes, and it revalidates against the
  live `information_schema` rather than a hardcoded list. A stale list here
  rejects good files; it can never admit bad ones.

`503` is the state the repo ships in — the Airflow compose profile is off by
default — and the body says `make pipeline-up`, which the dashboard renders as a
setup state. Same posture as a missing `GROQ_API_KEY`.

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
make eval          # cd api && uv run python -m evals.run
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
| `LLM_DAILY_TOKEN_BUDGET` | `200000` | tokens per UTC day, summed over `platform_ops.llm_calls`; `/api/chat` answers 429 past it |
| `CHAT_RATE_LIMIT` | `10/minute` | per client IP, chat route only, slowapi syntax |
| `AIRFLOW_BASE_URL` | `http://localhost:8081` | the published host port; compose overrides it to `http://airflow:8080` |
| `AIRFLOW_USERNAME` / `AIRFLOW_PASSWORD` | `admin` / `admin` | HTTP Basic against Airflow's stable REST API. Demo credentials — see `airflow/README.md` |
| `AIRFLOW_TIMEOUT_SECONDS` | `5` | short on purpose: these are small control-plane calls to a service on the same host |
| `UPLOADS_DIR` | *a temp dir* | where `/api/ingest` stages files; compose overrides it to `/srv/uploads`, a volume shared with Airflow |
| `MAX_UPLOAD_BYTES` | `5242880` | 5 MB, enforced while reading in chunks — a 50 MB file is a `413` after 5 MB |

`UPLOADS_DIR`'s default is a temp directory so the API starts anywhere, but note
that a **host-run API and a containerised Airflow do not share it**: the upload
succeeds and the DAG's first task then fails saying it cannot find the file. The
full ingestion loop wants everything in compose (`make stack-up && make
pipeline-up`).

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
  schemas_ops.py       pydantic response models for /api/observability/llm
  schema_context.py    the dbt-generated schema briefing the LLM writes SQL against
  sql_guard.py         sqlglot validation: one SELECT, no writes, a LIMIT
  llm.py               chat-completions wrapper + the typed failure taxonomy
  nl2sql.py            THE pipeline: prompts, PROMPT_VERSION, generate/guard/execute
  tracing.py           platform_ops.llm_calls: DDL, the trace write, the budget query
  rate_limit.py        the slowapi limiter + its 429 handler
  routers/chat.py      /api/chat    (HTTP only: codes, budget gate, trace row)
  routers/observability.py  /api/observability/llm
  airflow_client.py    Airflow REST: trigger a run, read its status; typed failures
  schemas_ingest.py    pydantic response models for /api/ingest — an orchestration run
  routers/ingest.py    /api/ingest/*  (validate, stage, hand off, report progress)
  main.py              app factory, CORS, /api/health, the 503 + upstream + 429 handlers
evals/golden.yaml      the questions + reference SQL — EDIT THIS, that's the exercise
evals/compare.py       result-set comparison: the scoring rules
evals/run.py           the runner (`make eval`); live + reference-check modes
tests/test_api.py           integration tests against the real local warehouse
tests/test_catalog.py       integration tests against the real dbt artifacts
tests/test_sql_guard.py     unit tests for the guard — no warehouse, no key, never skipped
tests/test_schema_context.py the briefing, built from the real artifacts
tests/test_chat.py          the unconfigured 503 + request validation; live path needs a key
tests/test_eval_compare.py  the eval scoring rules — pure functions, never skipped
tests/test_eval_runner.py   the runner's orchestration, provider stubbed (see below)
tests/test_observability.py tracing, the endpoint, the budget, the rate limit — no key needed
tests/test_ingest.py        upload validation + the Airflow handoff; needs no Airflow
```

`evals/` is a sibling of `app/`, not a module inside it: it is a test instrument,
so it is left out of the wheel (`packages = ["app"]`) and therefore out of the
Docker image, which serves the API and nothing else. `pyyaml` is a dev dependency
for the same reason.

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

`tests/test_eval_runner.py` is the **one** place the LLM is stubbed, and the
exception is deliberate: the *runner* is under test, not the model. Does a correct
answer score as correct, does a differently-aliased one still count, does an
arity mismatch fail with a readable reason, do the calls land in `llm_calls`
tagged `source='eval'`, and does a broken golden file exit 1 — none of which
depends on which SQL the stub returns, and all of which is otherwise unverified
in the state this repo ships in (no key). What stays unverified without a key is
the only thing a stub genuinely cannot check: whether a real model, given this
briefing, writes SQL that answers the question.

`tests/test_observability.py` covers the whole control plane — tracing, the
observability aggregation, the token budget, the rate limit — and **needs no API
key**. Where a test has to get past the is-configured gate it patches
`llm.is_configured` rather than requiring a key, which is safe precisely because
each of those tests asserts the request is rejected *before* the LLM call: that
ordering is the property under test, and nothing in the file can spend a token.
Rows are seeded with plain INSERTs into the real table, so what is asserted is the
aggregation (today vs yesterday, tokens summed, budget arithmetic) against inputs
a real run would take a day to produce.
