"""Pipeline observability: the last dbt run, from run_results.json.

`dbt build` writes one result per node it executed — status, wall-clock time,
and dbt's message when something went wrong. That file is the whole source
here; there is no run history, because dbt overwrites it every invocation.
(Keeping history would mean archiving the file per run somewhere — a different
feature, and one that needs somewhere to put it.)

Two things worth knowing about the artifact:

* Statuses are not one enum. Models, seeds and snapshots report
  success/error/skipped; tests report pass/fail/warn. `counts` carries all six
  so the UI can say "12 models built, 39 tests passed" without knowing that.
* Ephemeral models never appear. They are compiled into their consumers as CTEs
  rather than executed, so this project's 63 nodes produce 60 results.
"""

from typing import Any

from fastapi import APIRouter

from ..dbt_artifacts import load_manifest, load_run_results
from ..schemas_catalog import LatestRun, RunCounts, RunResultRow

router = APIRouter(prefix="/api/runs", tags=["runs"])

# dbt status -> field on RunCounts. `pass` is a Python keyword, hence pass_.
COUNT_FIELDS = {
    "success": "success",
    "error": "error",
    "skipped": "skipped",
    "pass": "pass_",
    "fail": "fail",
    "warn": "warn",
}


def _identify(unique_id: str, manifest_nodes: dict[str, Any]) -> tuple[str, str]:
    """(name, resource_type) for a result row.

    run_results.json carries only the unique_id, so the readable name comes from
    the manifest. The fallback parses the id itself — `model.anz_banking.foo` is
    type.package.name — which keeps this endpoint working if run_results is
    newer than the manifest beside it.
    """
    node = manifest_nodes.get(unique_id)
    if node is not None:
        return node["name"], node["resource_type"]
    parts = unique_id.split(".")
    return parts[-1], parts[0]


@router.get("/latest", summary="Status and timings of the last dbt build")
def get_latest_run() -> LatestRun:
    run_results = load_run_results()
    manifest_nodes = load_manifest()["nodes"]

    rows: list[RunResultRow] = []
    counts: dict[str, int] = {}
    for result in run_results["results"]:
        unique_id = result["unique_id"]
        name, resource_type = _identify(unique_id, manifest_nodes)
        status = result["status"]
        field = COUNT_FIELDS.get(status)
        if field is not None:
            counts[field] = counts.get(field, 0) + 1
        rows.append(
            RunResultRow(
                unique_id=unique_id,
                name=name,
                resource_type=resource_type,
                status=status,
                execution_time=result.get("execution_time") or 0.0,
                message=result.get("message"),
            )
        )

    return LatestRun(
        generated_at=run_results["metadata"]["generated_at"],
        elapsed_total=run_results["elapsed_time"],
        counts=RunCounts(**counts),
        # Ordered as dbt ran them, which is dependency order — the same order
        # the build log reads in. The client sorts failures first for display;
        # the API keeps the run's own narrative.
        results=rows,
    )
