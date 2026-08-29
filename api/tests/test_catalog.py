"""Integration tests for the catalog, lineage and runs endpoints.

Same bargain as test_api.py: no mocks. Those tests need a built warehouse, these
need built *artifacts* — the JSON dbt writes to `anz_banking/target/`. A fixture
manifest committed to the repo would drift from the dbt project the moment a
model moved folders, and the whole point of these endpoints is that they agree
with the real one.

So: `make local-build && make local-docs`, then `make api-test`.

The expected figures are the committed dbt project (24 catalogued nodes, 39
tests). Add a model, and they change with it.
"""

import pytest
from fastapi.testclient import TestClient

from app.dbt_artifacts import ArtifactsUnavailable, load_manifest, load_run_results
from app.main import app

# 15 models + 8 seeds + 1 snapshot. Sources are excluded — they are inputs dbt
# reads, not things it builds — but they DO appear in the lineage graph.
EXPECTED_CATALOG_NODES = 24
EXPECTED_TEST_NODES = 39
# The three ephemeral int_* models are compiled into their consumers as CTEs
# rather than executed, so they are in the manifest but not in run_results.
EXPECTED_RUN_RESULTS = 60
LAYERS = {"staging", "intermediate", "marts", "seed", "snapshot", "source", "unknown"}


def _artifacts_available() -> bool:
    try:
        load_manifest()
        load_run_results()
    except ArtifactsUnavailable:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _artifacts_available(),
    reason="dbt artifacts missing — run `make local-build && make local-docs`",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def models(client: TestClient) -> list[dict]:
    response = client.get("/api/catalog/models")
    assert response.status_code == 200
    return response.json()


# --- /api/catalog/models ------------------------------------------------------


def test_lists_every_model_seed_and_snapshot(models: list[dict]) -> None:
    assert len(models) == EXPECTED_CATALOG_NODES
    assert {model["resource_type"] for model in models} == {"model", "seed", "snapshot"}
    assert all(model["layer"] in LAYERS for model in models)
    # Tests are properties of a node, not rows in the catalog.
    assert not any(model["unique_id"].startswith("test.") for model in models)


def test_the_central_fact_is_in_the_marts_layer(models: list[dict]) -> None:
    fact = next(model for model in models if model["name"] == "fct_collection_cases")
    assert fact["layer"] == "marts"
    assert fact["resource_type"] == "model"
    assert fact["materialization"] == "table"
    assert fact["schema"] == "analytics_marts"
    assert fact["description"], "fct_collection_cases should be documented in _marts.yml"
    # 18 columns come from catalog.json; the manifest alone would report 3.
    assert fact["column_count"] == 18
    assert fact["test_count"] > 0


def test_layers_come_from_the_folder_or_the_resource_type(models: list[dict]) -> None:
    by_name = {model["name"]: model for model in models}
    assert by_name["stg_collections__cases"]["layer"] == "staging"
    assert by_name["int_ptp_per_case"]["layer"] == "intermediate"
    assert by_name["raw_accounts"]["layer"] == "seed"
    assert by_name["collection_cases_snapshot"]["layer"] == "snapshot"


def test_ephemeral_models_are_listed(models: list[dict]) -> None:
    """They are real nodes in the DAG even though nothing is materialised."""
    ephemeral = [model for model in models if model["materialization"] == "ephemeral"]
    assert {model["name"] for model in ephemeral} == {
        "int_ptp_per_case",
        "int_contacts_per_case",
        "int_payments_per_account",
    }


def test_every_test_in_the_project_is_attributed_to_exactly_one_node(
    models: list[dict],
) -> None:
    """A `relationships` test depends on two models; it must be counted once.

    Summing test_count is how that is checked: attributing by depends_on rather
    than attached_node would total 45 for 39 tests.
    """
    assert sum(model["test_count"] for model in models) == EXPECTED_TEST_NODES


# --- /api/catalog/models/{name} ----------------------------------------------


def test_model_detail_merges_manifest_docs_with_warehouse_types(
    client: TestClient,
) -> None:
    detail = client.get("/api/catalog/models/fct_collection_cases").json()

    assert detail["layer"] == "marts"
    assert len(detail["columns"]) == 18
    case_id = next(column for column in detail["columns"] if column["name"] == "case_id")
    # data_type is catalog.json's; the tests are the manifest's.
    assert case_id["data_type"] == "integer"
    assert set(case_id["tests"]) == {"unique", "not_null"}

    assert "stg_collections__cases" in detail["depends_on"]
    assert "collections_performance" in detail["referenced_by"]
    assert detail["raw_sql"].strip().startswith("--")
    # Jinja is gone from the compiled SQL: `ref()` has become a real relation.
    assert "{{" in detail["raw_sql"]
    assert "{{" not in detail["compiled_sql"]


def test_detail_shows_table_level_tests_separately(client: TestClient) -> None:
    """Tests with no column_name would otherwise be invisible on the page."""
    detail = client.get("/api/catalog/models/stg_collections__cases").json()
    assert detail["table_tests"] == ["assert_resolved_cases_have_resolved_date"]
    column_tests = sum(len(column["tests"]) for column in detail["columns"])
    assert column_tests + len(detail["table_tests"]) == detail["test_count"]


def test_seed_and_source_parents_are_deduplicated(client: TestClient) -> None:
    """Every raw table here is both a seed and a declared source.

    The staging models select from the source and carry a `-- depends_on:
    ref(seed)` comment for ordering, so both are parents of the same node under
    the same name. The link list shows it once.
    """
    detail = client.get("/api/catalog/models/stg_collections__cases").json()
    assert detail["depends_on"] == ["raw_collection_cases"]


def test_seeds_have_no_sql(client: TestClient) -> None:
    detail = client.get("/api/catalog/models/raw_accounts").json()
    assert detail["raw_sql"] is None
    assert detail["compiled_sql"] is None
    assert detail["depends_on"] == []
    assert detail["referenced_by"] == ["stg_collections__accounts"]


def test_unknown_model_returns_404(client: TestClient) -> None:
    response = client.get("/api/catalog/models/no_such_model")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


# --- /api/catalog/lineage -----------------------------------------------------


@pytest.fixture(scope="module")
def lineage(client: TestClient) -> dict:
    response = client.get("/api/catalog/lineage")
    assert response.status_code == 200
    return response.json()


def test_lineage_covers_sources_as_well_as_built_nodes(lineage: dict) -> None:
    # 24 catalogued nodes + 8 sources.
    assert len(lineage["nodes"]) == EXPECTED_CATALOG_NODES + 8
    assert {node["layer"] for node in lineage["nodes"]} <= LAYERS
    assert sum(1 for node in lineage["nodes"] if node["layer"] == "source") == 8


def test_lineage_has_the_staging_to_fact_edge(lineage: dict) -> None:
    stg = "model.anz_banking.stg_collections__cases"
    fact = "model.anz_banking.fct_collection_cases"
    edges = {(edge["source"], edge["target"]) for edge in lineage["edges"]}
    assert (stg, fact) in edges
    # ...and the fact carries on into the KPI mart.
    assert (fact, "model.anz_banking.collections_performance") in edges


def test_every_edge_endpoint_is_a_node_in_the_graph(lineage: dict) -> None:
    """No dangling edges — a DAG renderer would drop or crash on them."""
    ids = {node["id"] for node in lineage["nodes"]}
    for edge in lineage["edges"]:
        assert edge["source"] in ids, edge
        assert edge["target"] in ids, edge


def test_lineage_excludes_tests(lineage: dict) -> None:
    assert not any(node["id"].startswith("test.") for node in lineage["nodes"])


def test_ephemeral_models_keep_their_edges(lineage: dict) -> None:
    """dbt inlines them as CTEs, but they are nodes with parents and children."""
    edges = {(edge["source"], edge["target"]) for edge in lineage["edges"]}
    assert (
        "model.anz_banking.stg_collections__promises_to_pay",
        "model.anz_banking.int_ptp_per_case",
    ) in edges
    assert (
        "model.anz_banking.int_ptp_per_case",
        "model.anz_banking.fct_collection_cases",
    ) in edges


# --- /api/runs/latest ---------------------------------------------------------


@pytest.fixture(scope="module")
def latest_run(client: TestClient) -> dict:
    response = client.get("/api/runs/latest")
    assert response.status_code == 200
    return response.json()


def test_latest_run_has_a_result_per_executed_node(latest_run: dict) -> None:
    assert len(latest_run["results"]) == EXPECTED_RUN_RESULTS
    assert latest_run["elapsed_total"] > 0
    assert latest_run["generated_at"]
    assert all(result["execution_time"] >= 0 for result in latest_run["results"])


def test_counts_tie_out_with_the_results(latest_run: dict) -> None:
    """Model statuses and test statuses are different enums; both are tallied.

    Checked against the results themselves rather than against hardcoded totals,
    so this still holds on a run where something failed.
    """
    counts = latest_run["counts"]
    results = latest_run["results"]

    tests = [row for row in results if row["resource_type"] == "test"]
    built = [row for row in results if row["resource_type"] != "test"]

    assert counts["pass"] + counts["fail"] + counts["warn"] == len(
        [row for row in tests if row["status"] in {"pass", "fail", "warn"}]
    )
    assert counts["success"] + counts["error"] == len(
        [row for row in built if row["status"] in {"success", "error"}]
    )
    assert sum(counts.values()) == len(results), "every status should be tallied"


def test_a_clean_build_reports_no_failures(latest_run: dict) -> None:
    """The committed seeds pass every test, so a fresh build is all green."""
    counts = latest_run["counts"]
    assert counts["error"] == 0
    assert counts["fail"] == 0
    assert counts["pass"] == EXPECTED_TEST_NODES
    assert counts["success"] == EXPECTED_RUN_RESULTS - EXPECTED_TEST_NODES


def test_results_are_named_from_the_manifest(latest_run: dict) -> None:
    """run_results.json only carries unique_ids; the names are joined on."""
    fact = next(
        row
        for row in latest_run["results"]
        if row["unique_id"] == "model.anz_banking.fct_collection_cases"
    )
    assert fact["name"] == "fct_collection_cases"
    assert fact["resource_type"] == "model"
    assert fact["status"] == "success"


def test_ephemeral_models_are_absent_from_the_run(latest_run: dict) -> None:
    """Nothing executed them, so dbt has nothing to report — not even 'skipped'."""
    names = {row["name"] for row in latest_run["results"]}
    assert "int_ptp_per_case" not in names
    assert "fct_collection_cases" in names
