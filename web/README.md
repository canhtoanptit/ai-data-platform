# Collections dashboard

A **Vite + React + TypeScript** front end over the [`api/`](../api) read layer —
the last hop of the platform: files → warehouse → dbt marts → REST → screen.

Nothing here queries the warehouse. Every number on the page is a mart column the
API handed over as JSON.

## The six pages

Client-side routing (`react-router-dom`), one nav in the header. Real URLs, not
hash routes — both servers fall back to `index.html` for unknown paths, so
`/lineage` survives a refresh and can be shared.

### Dashboard (`/`)

| Section | Endpoint |
|---------|----------|
| Live health dot in the header (polled every 30s) | `/api/health` |
| Six KPI tiles: cases, open cases, delinquent amount, cure / PTP-kept / RPC rates | `/api/metrics/summary` |
| Delinquent amount by delinquency bucket, grouped by team | `/api/metrics/performance` |
| Cure rate by team | `/api/metrics/performance` |
| Case table with a status filter (open / resolved / written off / all) | `/api/cases?status=…` |

### Catalog (`/catalog`)

Searchable, layer-filtered list of every model, seed and snapshot; click one for
its description, columns (name, warehouse type, description, tests),
`depends_on` / `referenced_by` as links, and collapsible raw + compiled SQL.
Reads `/api/catalog/models` and `/api/catalog/models/{name}`.

The selected model lives in the URL (`/catalog?model=fct_collection_cases`)
rather than in component state, which buys three things at no cost: the lineage
page can link straight to a model, the link is shareable, and the back button
walks the models you looked at.

### Lineage (`/lineage`)

The dbt DAG in [`@xyflow/react`](https://reactflow.dev), laid out left to right
by [dagre](https://github.com/dagrejs/dagre), nodes coloured by layer with a
legend. Click a node for an info panel and a link into the catalog. Reads
`/api/catalog/lineage`.

The layout is a **pure function** —
[`src/lib/dagreLayout.ts`](./src/lib/dagreLayout.ts), graph in / positions out,
no React and no xyflow imports — so it is unit-tested without a DOM and the page
component does nothing but render. Two things it has to get right: dagre returns
node *centres* while React Flow positions by *top-left*, and an edge pointing at
a node dagre was not given is silently invented as an unlabelled ghost box.

Every raw table appears twice on the left, once as a seed and once as a source.
That is not a bug in the drawing: this project declares both (the
`-- depends_on: ref(seed)` comment in each `stg_` model is what stops
`dbt build` racing the seed), and the graph shows what dbt actually holds. The
catalog's link lists dedupe them; the DAG does not.

### Runs (`/runs`)

Summary tiles (models built, build failures, tests passed, tests failed, elapsed,
run at) over a table of all 60 executed nodes with coloured status badges, failed
rows sorted first. Reads `/api/runs/latest`.

dbt statuses are two enums, not one — models report `success | error | skipped`,
tests report `pass | fail | warn` — so
[`src/lib/runStatus.ts`](./src/lib/runStatus.ts) maps both onto a badge tone.
An unrecognised status is `neutral`, never green: dbt has added statuses over
releases (`no-op`, `reused`) and a new one must not be reported as a success.

### Ask AI (`/chat`)

Type a question in English; the API writes the SQL, runs it read-only and hands
back the rows. Reads `POST /api/chat` — the only write in
[`src/api/client.ts`](./src/api/client.ts).

Each reply shows three things, in this order: the prose answer, the result table,
and a collapsible **View SQL** block. The SQL is always there, because "what did
it actually run?" is the first question anyone asks of a text-to-SQL feature, and
being able to check it is what makes the answer worth trusting.

Four failure modes, all rendered **in the thread** rather than as a page banner —
the question they belong to is right above them, and the next question should
still be askable:

| Status | Rendered as |
|--------|-------------|
| `503` | a setup state: get a free key at console.groq.com, put `GROQ_API_KEY` in `.env`, restart the API |
| `429` | "the free tier is rate limited — try again in a moment" |
| `422` | the SQL the model tried, plus the validator's or the warehouse's complaint |
| anything else | the API's `detail`, as an alert |

`503` is the state the repo ships in, since the key is not committed — so it is
treated as an unconfigured feature, not an error, exactly like `QueryState`'s
"dbt hasn't run yet". Four clickable example questions stand in for an empty
thread.

There are two ways to get a `503` here, and they need different instructions:
no API key, or no dbt artifacts (the schema briefing the model writes SQL against
is built from them). The API's `detail` says which, so the UI keys off it rather
than off the status code alone.

Three decisions worth naming:

- **Conversation memory is a deliberate non-goal for v1.** The endpoint is
  single-turn: every question is answered from the schema alone, so follow-ups
  ("and by team?") will not work. The transcript is a local `useState` array of
  *turns* — a question and its own reply, discriminated on `status` so the
  renderer cannot read an answer off a turn that failed — and it is gone on
  reload. Memory would mean sending prior turns to the model and deciding what
  to do when the context fills; the interesting problem here is the SQL.
- **Plain `useState` and an `async` call, not TanStack Query.** Asking a question
  is an action with a one-off result, not shared server state worth caching
  under a key — the one place in the app where the query cache is the wrong tool.
- **Result cells are printed unformatted**, unlike every other table here. Those
  know what their column means; this SQL is written per question, so a number
  could be a case id, a dollar amount or a percentage, and thousands separators
  on `case_id 7001` would be actively wrong. Numbers are right-aligned so they
  still line up.

### Ingest (`/ingest`)

The one page that **starts** something. Pick a destination table, drag a file
onto the drop zone (or use the picker), and watch the pipeline it triggers:
`POST /api/ingest` → 202 + a run id → poll `/api/ingest/runs/{id}` until it
settles.

| Status | Rendered as |
|--------|-------------|
| `503` | a setup state: "the pipeline service is not running", with `make pipeline-up` |
| `422` (header mismatch) | a **column diff** — missing, unexpected, expected, found |
| `422` / `413` (other) | the API's `detail`, as an alert |
| a settled run | a banner (green/red/neutral), a row per DAG task with its state and duration, and links to `/runs` and `/` |

Four decisions worth naming:

- **Polling is driven by the server's `is_running` flag**, not by the client
  reading Airflow's state names. `refetchInterval` returns `false` once that flips
  and the query stops on its own — and two different clients cannot disagree
  about when a run is over.
- **The run panel does not use `QueryState`.** Its 503 branch says "no dbt
  artifacts yet — run `make local-build`", which is right for the catalog pages
  and wrong here; a 503 on this query means Airflow stopped. So it reuses the
  same renderer the upload failure does.
- **A failed run is framed as the quality gate, not as a broken page.** Upload
  the same file twice and dbt's `unique` test fails the build on purpose; the
  banner explains that the bad rows are in the raw landing table and nothing
  downstream was published. The page says so *before* you try it, too.
- **The file input is visually hidden, not `display: none`.** A
  `display: none` input leaves the accessibility tree and loses keyboard focus,
  which would break the `<label>` that stands in for it — so it is clipped
  instead, and a `:focus-visible` rule puts the focus ring on the label.

Two sample files ([`public/samples/`](./public/samples)) are linked from the
page — the same five payments as CSV and as pipe-delimited, so the delimiter
inference is demonstrable in one click.

### When dbt hasn't run yet

The last three pages read dbt's build artifacts, so the API answers **503** until
`make local-build` has run. `QueryState` special-cases it: a 503 renders as a
friendly empty state carrying the commands to run, not as an error. Everything
else still renders — the dashboard reads the warehouse and is unaffected.

Two things worth knowing about the numbers:

- **A null rate renders as `–`, never `0%`.** The API returns `null` when a rate's
  denominator is empty (no promises to pay yet, say); "unknown" and "zero
  percent" are different answers and the UI keeps them apart.
- **Bucket order is declared in code** ([`src/lib/buckets.ts`](./src/lib/buckets.ts)),
  not taken from the API. `current → 1-30 → 31-60 → 61-90 → 90+` is ordinal and
  sorts neither alphabetically nor numerically.

Server state is [TanStack Query](https://tanstack.com/query): each section owns
its own `useQuery`, caching, loading and error states come from the query, and the
filter dropdown works by changing the query *key* rather than by re-fetching by
hand. Both charts share the key `['performance']`, so the endpoint is fetched once
and drawn twice.

## Package manager: pnpm

**pnpm 10, not npm** — for two install-time protections, both configured in
[`pnpm-workspace.yaml`](./pnpm-workspace.yaml):

- `minimumReleaseAge: 10080` — nothing published in the last 7 days is
  installable. Malicious versions of real packages are the usual npm
  supply-chain attack and are typically caught within hours, so a week of
  quarantine buys a lot for free. It bites in practice: the lockfile holds
  `@tanstack/react-query` 5.101.4 because 5.102.7 was hours old when this was
  built.
- `onlyBuiltDependencies: []` — pnpm 10 blocks dependencies' install scripts
  (arbitrary code at install time) unless a package is allowlisted by name.
  Nothing here needs one, so the list stays empty.

The pnpm version is pinned in `package.json`'s `packageManager` field, so
[corepack](https://nodejs.org/api/corepack.html) users and the Docker build get
the same one:

```bash
corepack enable && corepack prepare pnpm@10.34.5 --activate   # or: npm i -g pnpm
```

## Running it

The dashboard shows what dbt has already built, so build the marts first
(`make stack-up && make local-build && make local-docs` from the repo root).
`local-docs` is only needed for the Catalog page's column types; without it the
type column reads `–` and everything else still works.

### Dev mode — hot reload

```bash
make web-dev          # or: cd web && pnpm install && pnpm dev
```

<http://localhost:5173>. Needs the API up on port 8000 (`make stack-up` or
`make api-dev`).

### Docker mode — the built app

```bash
make stack-up         # postgres + api + web, waits until healthy
```

<http://localhost:3000>. Rebuild after changing the source:
`docker compose build web && docker compose up -d web`.

### Tests

```bash
make web-test         # or: cd web && pnpm test
```

Vitest + Testing Library: the formatters (including the null-rate rule), the
bucket-order helper, the layer→colour and dbt-status→badge mappings, the dagre
layout helper (a pure function, so no DOM needed), the KPI tiles and run summary
tiles rendered with mocked payloads, the chat thread (a mocked exchange with its
SQL block, the no-prose fallback, and each of the 503/429/422 states), and
`askChat`'s request serialisation and error bodies against a stubbed `fetch`.

## How `/api` is reached in each mode

The app never contains an API base URL — it calls relative paths like
`/api/metrics/summary` ([`src/api/client.ts`](./src/api/client.ts)) and lets the
server it was loaded from forward them. Two servers, one contract:

```
dev      browser → :5173 Vite dev server ─proxy─→ localhost:8000  (vite.config.ts)
docker   browser → :3000 nginx           ─proxy─→ api:8000        (nginx.conf)
```

`api:8000` rather than `localhost:8000` in the container because inside the
compose network a service is reachable by its service name; `localhost` there
would be nginx itself.

Because the browser only ever sees one origin, there is no CORS preflight in
either mode. (The API does allow both origins anyway — useful if you point the
dev server at a remote API.)

## Layout

```
src/
  api/         client.ts (typed fetch fns), types.ts (mirrors the API's pydantic models)
  lib/         format.ts (formatters), buckets.ts (bucket order), palette.ts (colours),
               runStatus.ts (dbt + Airflow status -> badge tone), dagreLayout.ts (DAG layout)
  pages/       DashboardPage, CatalogPage, LineagePage, RunsPage, ChatPage, IngestPage
  components/  Header (+ nav), KpiTiles, the two charts, CasesTable, ModelDetail,
               RunSummary, Badges, QueryState, Panel, ChatThread, IngestRunPanel
  index.css    the whole stylesheet: one palette, declared as CSS custom properties
public/
  samples/     two downloadable demo extracts for the Ingest page (CSV + pipe)
```

`runStatus.ts` holds **two** status maps, not one. dbt reports
`success | error | skipped` and `pass | fail | warn`; Airflow reports
`success | failed | running | queued | upstream_failed | skipped`. They share
exactly one word, and merging them would produce a dictionary that is right about
neither system's enum.

Two notes on the styling, both deliberate:

- **No CSS framework.** One hand-written stylesheet with the palette as custom
  properties — nothing to configure, nothing to learn to read it.
- **Chart colours live in `src/lib/palette.ts`, not in CSS.** Recharts writes
  colours into SVG presentation attributes, where `var()` is not reliably
  supported. Same palette, two consumers; both files say so.

The palette is two series colours (blue, orange) plus neutral grays, checked for
colourblind separation against the page surface. Charts are flat fills, labelled
axes, tooltips on, money shortened to `$16.6k` on axes and exact in tooltips.

`palette.ts` also holds seven **layer** colours for the lineage DAG, and the
distinction from the chart series matters: a bar can only be read by colour, so
three validated-as-a-set colours is the safe limit there. A DAG node carries its
name in text, its layer in the legend and its position in the left-to-right
flow, so colour is a fourth, redundant cue — which is what makes seven of them
acceptable. Same rule on the Runs page: every badge contains the status word.
