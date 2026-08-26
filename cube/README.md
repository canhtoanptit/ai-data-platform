# Consumption layer — Cube (semantic layer over the gold marts)

Cube sits between the dbt **gold marts** in Snowflake and an application,
exposing consistent metrics through REST / GraphQL / SQL APIs and serving app
queries from cached **pre-aggregations** (sub-second, high-concurrency, low
warehouse cost) instead of hitting Snowflake per request.

```
Snowflake MARTS (fct_collection_cases, dim_agents)
        │
        ▼
      Cube  ── semantic model (measures: cure_rate, ptp_kept_rate, …)
        │     └── pre-aggregations (materialized rollups, refreshed hourly)
        ▼
   your app  ── REST / GraphQL / SQL API
```

## Files

| Path | What it is |
|------|-----------|
| `model/cubes/collection_cases.yml` | Semantic model over `fct_collection_cases`: dimensions, measures (`cure_rate`, `ptp_kept_rate`), a pre-aggregation |
| `model/cubes/agents.yml` | `dim_agents`, joined from `collection_cases` |
| `docker-compose.yml` | Run Cube locally |
| `.env.example` | Snowflake connection env vars |

## Run it

```bash
cd cube
cp .env.example .env      # fill in Snowflake details (use a read-only role)
docker compose up
```

Open the **Playground** at http://localhost:4000 — build a query visually
(e.g. measure `collection_cases.cure_rate` by dimension `agents.team`) and copy
the generated REST/GraphQL/SQL.

> **Schema-name note:** the example points at `ANZ_COLLECTIONS.MARTS.*`. dbt's
> default `generate_schema_name` prepends your target schema in dev (e.g.
> `DBT_TOAN_MARTS`). Either adjust `sql_table:` to match, or override
> `generate_schema_name` in the dbt project so marts land in a clean `MARTS`
> schema (the common production pattern). Give Cube a **read-only** role.

## How an application queries it

**REST** — POST/GET a JSON query to `/cubejs-api/v1/load`:

```bash
curl "http://localhost:4000/cubejs-api/v1/load" \
  -H "Authorization: <api-token>" \
  -G --data-urlencode 'query={
    "measures": ["collection_cases.cure_rate", "collection_cases.total_delinquent_amount"],
    "dimensions": ["agents.team", "collection_cases.delinquency_bucket"]
  }'
```

**GraphQL** — `POST /cubejs-api/graphql` (typed queries for a JS/TS frontend).

**SQL API** — connect any Postgres client / BI tool to `localhost:15432` and
query cubes as if they were tables:

```sql
SELECT team, delinquency_bucket, cure_rate
FROM collection_cases
CROSS JOIN agents          -- Cube resolves the modeled join
GROUP BY 1, 2;
```

A frontend typically uses `@cubejs-client/react` (or plain REST) to render
charts — the metric logic stays in the model, not scattered across the app.

## When Cube vs. alternatives

- **Cube** → embedded / customer-facing analytics needing consistent metrics,
  caching, and an API. Pre-aggregations are the reason to reach for it.
- **dbt Semantic Layer (MetricFlow)** → metrics defined in dbt; more dbt-native,
  weaker caching for app serving.
- **BI tool direct** (Power BI / Metabase) → analyst dashboards, not apps.
- **Serving DB / reverse ETL** → operational, low-latency app screens (don't
  serve those from the OLAP warehouse). See `../ARCHITECTURE.md`.
