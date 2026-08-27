# Collections dashboard

A **Vite + React + TypeScript** front end over the [`api/`](../api) read layer —
the last hop of the platform: files → warehouse → dbt marts → REST → screen.

Nothing here queries the warehouse. Every number on the page is a mart column the
API handed over as JSON.

## What it shows

| Section | Endpoint |
|---------|----------|
| Live health dot in the header (polled every 30s) | `/api/health` |
| Six KPI tiles: cases, open cases, delinquent amount, cure / PTP-kept / RPC rates | `/api/metrics/summary` |
| Delinquent amount by delinquency bucket, grouped by team | `/api/metrics/performance` |
| Cure rate by team | `/api/metrics/performance` |
| Case table with a status filter (open / resolved / written off / all) | `/api/cases?status=…` |

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
(`make stack-up && make local-build` from the repo root).

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
bucket-order helper, and the KPI tiles rendered with a mocked summary payload.

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
  api/         client.ts (typed fetch fns), types.ts (mirrors api/app/schemas.py)
  lib/         format.ts (money/rate formatters), buckets.ts (bucket order), palette.ts
  components/  Header, KpiTiles, the two charts, CasesTable, QueryState, Panel
  index.css    the whole stylesheet: one palette, declared as CSS custom properties
```

Two notes on the styling, both deliberate:

- **No CSS framework.** One hand-written stylesheet with the palette as custom
  properties — nothing to configure, nothing to learn to read it.
- **Chart colours live in `src/lib/palette.ts`, not in CSS.** Recharts writes
  colours into SVG presentation attributes, where `var()` is not reliably
  supported. Same palette, two consumers; both files say so.

The palette is two series colours (blue, orange) plus neutral grays, checked for
colourblind separation against the page surface. Charts are flat fills, labelled
axes, tooltips on, money shortened to `$16.6k` on axes and exact in tooltips.
