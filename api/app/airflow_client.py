"""Talks to Airflow's stable REST API. Three calls, one auth header, no SDK.

The API layer's job in the ingestion loop is deliberately small: validate the
upload, stage the file, and hand off. Airflow owns the execution. That means this
module only ever needs to *trigger* a run and *read* its status — which is three
endpoints of the v1 API and a `httpx` client, rather than a dependency on
`apache-airflow` itself (a ~200 MB install that would drag its own SQLAlchemy and
Flask versions into this service's lockfile for no benefit).

Errors are typed by what the caller should do about them:

| Class                | Status | Means                                          |
|----------------------|--------|------------------------------------------------|
| `AirflowUnreachable` | 503    | Nothing is listening — the profile is probably off |
| `AirflowRejected`    | 502    | Airflow answered, but not with what we expected |
| `DagRunNotFound`     | 404    | Valid request, no such run                     |

503 rather than 500 for the first one is the same judgement the catalog endpoints
make about missing dbt artifacts: the server is fine, an optional dependency
isn't running, and the fix is one make target away — so the detail says which.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import status

from .config import get_settings

# The DAG this API triggers. Its counterpart is airflow/dags/file_ingest.py; the
# name is the contract between them.
DAG_ID = "file_ingest"

# Printed in the 503 body, so the dashboard can show the fix rather than "Service
# Unavailable". The wording is asserted by tests and rendered by the Ingest page.
PIPELINE_HINT = "start the pipeline profile: make pipeline-up"


class AirflowApiError(RuntimeError):
    """Base class, carrying the HTTP status this failure should become.

    The status lives on the exception rather than at each raise site so that one
    handler in main.py can translate the whole family — see
    `upstream_error_handler` there.
    """

    status_code = status.HTTP_502_BAD_GATEWAY


class AirflowUnreachable(AirflowApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    def __init__(self, base_url: str, reason: str) -> None:
        super().__init__(
            f"Airflow is not reachable at {base_url} ({reason}) — {PIPELINE_HINT}"
        )


class AirflowRejected(AirflowApiError):
    """Airflow was reachable and said no. Its own message is included."""


class DagRunNotFound(AirflowApiError):
    status_code = status.HTTP_404_NOT_FOUND


def _client() -> httpx.Client:
    """A configured client per call. No module-level singleton, on purpose.

    Two reasons. A cached client would capture the settings (and the base URL) at
    import time, which breaks the tests that point the API at a dead port. And
    these are two requests per page interaction against a service on the same
    host — connection pooling would save microseconds and cost a lifecycle to
    manage in a framework that has no shutdown hook wired up here.
    """
    settings = get_settings()
    return httpx.Client(
        base_url=settings.airflow_base_url.rstrip("/"),
        auth=(settings.airflow_username, settings.airflow_password),
        timeout=settings.airflow_timeout_seconds,
    )


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    settings = get_settings()
    try:
        with _client() as client:
            response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        # Connection refused, DNS failure (`airflow` does not resolve because the
        # profile is off), or a timeout. All of them are the same fact from the
        # caller's side: there is no orchestrator here right now.
        raise AirflowUnreachable(settings.airflow_base_url, type(exc).__name__) from exc

    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise DagRunNotFound(f"Airflow has no record of {path}")
    if response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ):
        # Worth its own message: everything is running, the credentials are
        # simply not the ones Airflow was started with.
        raise AirflowRejected(
            f"Airflow refused the API's credentials ({response.status_code}). "
            "AIRFLOW_USERNAME / AIRFLOW_PASSWORD must match the admin user the "
            "airflow service creates."
        )
    if response.is_error:
        # Airflow's own error bodies are `{"detail": ..., "title": ...}`. Its
        # message is the useful part (e.g. "DAG is paused"), and it is Airflow's
        # own text rather than anything a client sent us, so passing it through
        # is safe.
        detail = ""
        try:
            body = response.json()
            detail = body.get("detail") or body.get("title") or ""
        except ValueError:
            detail = response.text[:200]
        raise AirflowRejected(f"Airflow answered {response.status_code}: {detail}")

    return response.json()


def trigger_file_ingest(conf: dict[str, Any], dag_run_id: str) -> dict[str, Any]:
    """Start a `file_ingest` run and return Airflow's dag-run object.

    `conf` is the whole interface between the two services: `{table, file_path,
    delimiter}`. Note what is NOT in it — the file's contents. Only the path into
    the shared volume travels, so Airflow's metadata database never stores a
    5 MB CSV and the DAG reads the bytes straight off disk.

    The run id is *ours*, not Airflow's. Left to itself Airflow names the run
    `manual__2026-09-03T05:05:41.823037+00:00`, which then has to survive being a
    path segment in the poll URL through a browser, nginx and httpx — `+` and `:`
    in a path work until the day some proxy decides otherwise. The caller's id is
    `[A-Za-z0-9_]` only, and it is legible in the Airflow UI as a bonus.
    """
    return _request(
        "POST",
        f"/api/v1/dags/{DAG_ID}/dagRuns",
        json={"dag_run_id": dag_run_id, "conf": conf},
    )


def get_dag_run(dag_run_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/v1/dags/{DAG_ID}/dagRuns/{dag_run_id}")


def get_task_instances(dag_run_id: str) -> list[dict[str, Any]]:
    body = _request(
        "GET", f"/api/v1/dags/{DAG_ID}/dagRuns/{dag_run_id}/taskInstances"
    )
    return body.get("task_instances", [])
