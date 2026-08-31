"""Tests for the schema briefing the LLM writes SQL against.

Same bargain as test_catalog.py: the briefing is built from the *real* dbt
artifacts, because agreeing with the real project is the entire point of
generating it instead of writing it by hand. A committed fixture manifest would
drift the moment a mart gained a column — which is exactly the failure these
tests exist to catch. Skips itself if the artifacts are missing.

No API key is needed: building the prompt is pure artifact-reading, and none of
it calls the LLM.
"""

import pytest

from app.dbt_artifacts import ArtifactsUnavailable, load_manifest
from app.schema_context import DOMAIN_NOTES, MARTS_MODELS, build_schema_context


def _artifacts_available() -> bool:
    try:
        load_manifest()
    except ArtifactsUnavailable:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _artifacts_available(),
    reason="dbt artifacts missing — run `make local-build && make local-docs`",
)

# Models that must NOT be described: the briefing is the marts layer only, so
# these are the ones whose presence would mean the filter broke.
NON_MART_MODELS = (
    "stg_collections__cases",
    "stg_collections__agents",
    "stg_collections__customers",
    "int_payments_per_account",
    "int_contacts_per_case",
    "raw_collection_cases",
)


@pytest.fixture(scope="module")
def context() -> str:
    return build_schema_context()


@pytest.mark.parametrize("model", MARTS_MODELS)
def test_every_mart_is_described_and_schema_qualified(context: str, model: str) -> None:
    # Schema-qualified in the briefing because that is how the SQL must name it:
    # the prompt tells the model to write analytics_marts.<table>, and showing it
    # that way is more reliable than only saying it.
    assert f"table analytics_marts.{model}" in context


@pytest.mark.parametrize("model", NON_MART_MODELS)
def test_staging_and_raw_models_are_not_offered(context: str, model: str) -> None:
    """Narrowing the briefing to the marts is an accuracy decision, not cosmetic.

    Staging models have overlapping column names and no tested grain; offering
    them gives the model wrong-but-plausible options to choose from.
    """
    assert model not in context


def test_columns_carry_their_warehouse_types(context: str) -> None:
    """Types come from catalog.json, so the model knows what it can aggregate."""
    assert "- case_id integer" in context
    assert "- opened_date date" in context
    assert "- delinquent_amount numeric" in context
    assert "- is_delinquent boolean" in context


def test_all_eighteen_fact_columns_are_listed(context: str) -> None:
    """The manifest documents 3 of them; the union has to supply the rest.

    This is the assertion that would fail if merge_columns() were replaced by a
    manifest-only read — the model would silently lose 15 columns it can query.
    """
    fact_block = context.split("table analytics_marts.fct_collection_cases", 1)[1]
    fact_block = fact_block.split("\n\ntable ", 1)[0]
    assert len([line for line in fact_block.splitlines() if line.startswith("  - ")]) == 18


def test_model_descriptions_are_included(context: str) -> None:
    assert "One row per collections case" in context
    assert "Collections KPIs aggregated by agent team" in context


def test_column_descriptions_are_included_when_documented(context: str) -> None:
    """cure_rate_pct is the one documented mart column; its meaning matters."""
    assert "Percent of cases in the segment that were resolved" in context


@pytest.mark.parametrize("note", DOMAIN_NOTES)
def test_domain_notes_are_appended(context: str, note: str) -> None:
    """The value conventions dbt's metadata cannot express (0/1 ints, bucket order)."""
    assert note in context


def test_the_briefing_carries_no_row_data(context: str) -> None:
    """Structure and vocabulary only — warehouse contents never enter the prompt.

    Real values from the seeds (a customer name, an email domain, the portfolio
    total) are a cheap canary for that rule.
    """
    for value in ("olivia", "@example.com", "16630.75"):
        assert value not in context.lower()


def test_it_is_cached_against_the_artifacts(context: str) -> None:
    """Rebuilt only when dbt writes new artifacts — the mtime rule again."""
    assert build_schema_context() is context
