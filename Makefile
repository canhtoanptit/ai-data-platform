# Convenience wrapper so you never have to remember the flags.
# `uv run --env-file .env` loads your Snowflake creds into the environment,
# and we point dbt at the project + profiles dirs explicitly.

DBT   := uv run --env-file .env dbt
FLAGS := --project-dir anz_banking --profiles-dir .dbt

.PHONY: help deps debug seed run test build snapshot docs clean fresh \
        local-up local-build local-run local-test local-docs local-down \
        api-dev api-test web-dev web-test stack-up stack-down

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

deps:            ## Install dbt packages (dbt_utils)
	$(DBT) deps $(FLAGS)

debug:           ## Test the Snowflake connection + config
	$(DBT) debug $(FLAGS)

seed:            ## Load the CSVs in seeds/ into Snowflake
	$(DBT) seed $(FLAGS)

run:             ## Build all models (staging -> intermediate -> marts)
	$(DBT) run $(FLAGS)

test:            ## Run all data tests
	$(DBT) test $(FLAGS)

snapshot:        ## Run snapshots (SCD2 history)
	$(DBT) snapshot $(FLAGS)

build:           ## seed + run + snapshot + test, in dependency order
	$(DBT) build $(FLAGS)

docs:            ## Generate + serve the docs site (http://localhost:8080)
	$(DBT) docs generate $(FLAGS) && $(DBT) docs serve $(FLAGS)

clean:           ## Remove target/ and dbt_packages/
	$(DBT) clean $(FLAGS)

fresh: deps build ## First-time setup: install packages then build everything

# --- Local Postgres warehouse (docker compose) -------------------------------
# Same project, same models, different target. Snowflake can't run locally, so
# `--target local` builds everything into the postgres container instead.
# Usage: make local-up && make local-build

LOCAL := --target local

local-up:        ## Start the local Postgres warehouse and wait until healthy
	docker compose up -d --wait postgres

local-build:     ## seed + run + snapshot + test against local Postgres
	$(DBT) build $(FLAGS) $(LOCAL)

local-run:       ## Build all models against local Postgres
	$(DBT) run $(FLAGS) $(LOCAL)

local-test:      ## Run all data tests against local Postgres
	$(DBT) test $(FLAGS) $(LOCAL)

local-docs:      ## Generate catalog.json (warehouse column types) for the catalog page
	# --no-compile matters. Without it, `docs generate` runs a compile pass and
	# overwrites run_results.json with *compile* results — every node "success",
	# execution times of a few ms, and the 39 test pass/fail statuses gone. That
	# would silently gut the /runs observability page. --no-compile leaves the
	# build's run_results.json (and its compiled SQL in manifest.json) alone and
	# only writes catalog.json, so `local-build` then `local-docs` gives the API
	# all three artifacts with the numbers you actually ran.
	$(DBT) docs generate $(FLAGS) $(LOCAL) --no-compile

local-down:      ## Stop the local Postgres warehouse (keeps the data volume)
	docker compose down

# --- FastAPI read layer (api/) -----------------------------------------------
# api/ is its own uv project with its own lockfile — it shares nothing with the
# dbt tooling above except the Postgres it reads from. Hence `cd api` rather
# than the $(DBT) wrapper.

api-dev:         ## Run the API locally with autoreload (http://localhost:8000/docs)
	cd api && uv run uvicorn app.main:app --reload

api-test:        ## Run the API integration tests (needs the local warehouse built)
	cd api && uv run pytest

# --- React dashboard (web/) --------------------------------------------------
# Its own pnpm project, so `cd web` for the same reason api/ gets its own
# targets. pnpm rather than npm for two supply-chain settings in
# web/pnpm-workspace.yaml: a 7-day quarantine on newly published versions, and
# dependency install scripts blocked unless allowlisted.
# In dev, Vite proxies /api to localhost:8000 — so run `make api-dev` or
# `make stack-up` alongside it.

web-dev:         ## Run the dashboard with hot reload (http://localhost:5173)
	cd web && pnpm install && pnpm dev

web-test:        ## Run the dashboard unit + component tests (vitest)
	cd web && pnpm test

stack-up:        ## Start the whole stack (Postgres + API + dashboard), wait until healthy
	docker compose up -d --wait

stack-down:      ## Stop the whole stack (keeps the data volume)
	docker compose down
