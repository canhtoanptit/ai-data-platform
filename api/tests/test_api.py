"""Integration tests against the real local Postgres warehouse.

These are deliberately NOT unit tests with a mocked database. The whole point of
this layer is that the SQL matches what dbt built, and a mock would happily agree
with SQL that no warehouse accepts. So: run `make local-up && make local-build`,
then `make api-test`.

The expected numbers below are the committed seed data in anz_banking/seeds/
(8 cases). If a seed changes, these change with it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine
from app.main import app

EXPECTED_TOTAL_CASES = 8
EXPECTED_OPEN_CASES = 4
CASE_STATUSES = {"open", "resolved", "written_off"}


def _warehouse_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1 from analytics_marts.fct_collection_cases limit 1"))
    except Exception:  # noqa: BLE001 - any failure means "can't test against it"
        return False
    return True


# Module-level skip so this suite is a no-op (not a failure) in CI or on a
# machine where the warehouse container isn't running.
pytestmark = pytest.mark.skipif(
    not _warehouse_reachable(),
    reason="local Postgres marts unreachable — run `make local-up && make local-build`",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_db_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "detail": None}


def test_summary_shape_and_values(client: TestClient) -> None:
    response = client.get("/api/metrics/summary")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {
        "total_cases",
        "open_cases",
        "total_delinquent_amount",
        "cure_rate_pct",
        "ptp_kept_rate_pct",
        "rpc_rate_pct",
    }
    assert body["total_cases"] == EXPECTED_TOTAL_CASES
    assert body["open_cases"] == EXPECTED_OPEN_CASES
    assert body["total_delinquent_amount"] > 0
    for rate_key in ("cure_rate_pct", "ptp_kept_rate_pct", "rpc_rate_pct"):
        assert 0.0 <= body[rate_key] <= 100.0, rate_key


def test_summary_agrees_with_the_performance_mart(client: TestClient) -> None:
    """The two endpoints are the same metric at two grains; totals must tie out."""
    summary = client.get("/api/metrics/summary").json()
    rows = client.get("/api/metrics/performance").json()

    assert rows, "collections_performance is empty — did dbt build run?"
    assert sum(row["case_count"] for row in rows) == summary["total_cases"]
    assert sum(row["delinquent_amount"] for row in rows) == pytest.approx(
        summary["total_delinquent_amount"]
    )


def test_performance_rows_have_expected_columns(client: TestClient) -> None:
    rows = client.get("/api/metrics/performance").json()
    assert set(rows[0]) == {
        "team",
        "delinquency_bucket",
        "case_count",
        "delinquent_amount",
        "cured_cases",
        "written_off_cases",
        "cure_rate_pct",
        "ptp_kept_rate_pct",
        "rpc_rate_pct",
    }


def test_list_cases_defaults_to_newest_first(client: TestClient) -> None:
    cases = client.get("/api/cases").json()
    assert len(cases) == EXPECTED_TOTAL_CASES
    opened = [case["opened_date"] for case in cases]
    assert opened == sorted(opened, reverse=True)
    assert {case["case_status"] for case in cases} <= CASE_STATUSES


def test_status_filter_returns_only_that_status(client: TestClient) -> None:
    cases = client.get("/api/cases", params={"status": "open"}).json()
    assert cases, "expected at least one open case"
    assert all(case["case_status"] == "open" for case in cases)
    assert len(cases) == EXPECTED_OPEN_CASES


def test_bucket_filter_and_limit(client: TestClient) -> None:
    bucket = "90+ dpd"
    cases = client.get("/api/cases", params={"bucket": bucket, "limit": 2}).json()
    assert 0 < len(cases) <= 2
    assert all(case["delinquency_bucket"] == bucket for case in cases)


def test_offset_pages_without_overlap(client: TestClient) -> None:
    first = client.get("/api/cases", params={"limit": 3, "offset": 0}).json()
    second = client.get("/api/cases", params={"limit": 3, "offset": 3}).json()
    ids = {case["case_id"] for case in first} & {case["case_id"] for case in second}
    assert not ids, f"paging returned overlapping cases: {ids}"


def test_limit_above_max_is_rejected(client: TestClient) -> None:
    # limit is capped at 200; FastAPI validates it before any SQL runs.
    assert client.get("/api/cases", params={"limit": 201}).status_code == 422


def test_get_single_case(client: TestClient) -> None:
    case_id = client.get("/api/cases", params={"limit": 1}).json()[0]["case_id"]
    response = client.get(f"/api/cases/{case_id}")
    assert response.status_code == 200
    assert response.json()["case_id"] == case_id


def test_missing_case_returns_404(client: TestClient) -> None:
    response = client.get("/api/cases/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "case 9999 not found"


def test_agents_lists_all_four(client: TestClient) -> None:
    response = client.get("/api/agents")
    assert response.status_code == 200
    body = response.json()

    # Ordered by (team, agent_id): early_stage 101,102 then late_stage 103,104.
    assert [agent["agent_id"] for agent in body] == [101, 102, 103, 104]
    assert {agent["team"] for agent in body} == {"early_stage", "late_stage"}
    assert all(agent["agent_name"] for agent in body)
    assert set(body[0]) == {"agent_id", "agent_name", "team", "hire_date"}
