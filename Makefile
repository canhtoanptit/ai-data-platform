# Convenience wrapper so you never have to remember the flags.
# `uv run --env-file .env` loads your Snowflake creds into the environment,
# and we point dbt at the project + profiles dirs explicitly.

DBT   := uv run --env-file .env dbt
FLAGS := --project-dir anz_banking --profiles-dir .dbt

.PHONY: help deps debug seed run test build snapshot docs clean fresh \
        local-up local-build local-run local-test local-down \
        api-dev api-test stack-up stack-down

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

stack-up:        ## Start the whole stack (Postgres + API) and wait until healthy
	docker compose up -d --wait

stack-down:      ## Stop the whole stack (keeps the data volume)
	docker compose down
