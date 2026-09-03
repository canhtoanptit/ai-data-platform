"""Tests for /api/ingest — the validating front door, with no Airflow behind it.

Split the same way test_chat.py is, and for the same reason: almost everything
interesting about this endpoint happens *before* the external service is
involved. A file with the wrong header, an unknown table or a 9 MB upload is
rejected without a single HTTP call leaving the process, so those tests need no
orchestrator, no warehouse and no artifacts — and they are exactly the contract
the Ingest page relies on.

The one test that does reach the network points the client at a closed port, so
"Airflow is not running" is asserted as a real connection failure rather than a
mocked one. That is the state the repo ships in (the `pipeline` compose profile
is off by default), so it deserves to be tested for real.

What is deliberately NOT here is a happy path. A successful upload appends rows
to the warehouse and runs dbt; a test suite that mutates the demo data would
make every other suite's expected numbers a function of test ordering. The
API's half of the happy path — the exact `conf` it hands Airflow — is asserted
with a stubbed client instead, and the full loop is verified by running it.
"""

import io
import re
import socket

import pytest
from fastapi.testclient import TestClient

from app import airflow_client
from app.config import get_settings
from app.main import app
from app.routers.ingest import INGESTABLE, _safe_filename

PAYMENTS_HEADER = "payment_id,account_id,payment_date,amount,method"
PAYMENTS_CSV = f"{PAYMENTS_HEADER}\n9016,5001,2024-04-15,210.00,card\n"


def _closed_port() -> int:
    """A port nothing is listening on.

    Bind, read the port the OS chose, release it. Racy in theory; in practice the
    kernel does not hand the same ephemeral port straight back, and the payoff is
    a *connection refused* in microseconds rather than a five-second timeout
    against a port that might be firewalled.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """A client whose uploads go to a temp directory and whose Airflow is dead.

    Both overrides go through the environment plus a cache_clear, which is how
    `get_settings` is meant to be reconfigured — it is `lru_cache`d so the
    environment is read once per process, and a test that changes the
    environment has to say so.
    """
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIRFLOW_BASE_URL", f"http://127.0.0.1:{_closed_port()}")
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _post(client: TestClient, *, table="raw_payments", name="new_payments.csv",
          body: str | bytes = PAYMENTS_CSV, delimiter: str | None = None):
    data: dict[str, str] = {"table": table}
    if delimiter is not None:
        data["delimiter"] = delimiter
    content = body.encode() if isinstance(body, str) else body
    return client.post(
        "/api/ingest",
        data=data,
        files={"file": (name, io.BytesIO(content), "text/csv")},
    )


# --- the allow-list, which needs nothing at all -------------------------------


def test_tables_lists_the_allow_list(client: TestClient) -> None:
    response = client.get("/api/ingest/tables")
    assert response.status_code == 200
    body = response.json()

    assert [row["name"] for row in body] == list(INGESTABLE)
    payments = next(row for row in body if row["name"] == "raw_payments")
    # The page renders these as "your file must have this header", so the order
    # and the exact names are the contract, not an implementation detail.
    assert payments["columns"] == PAYMENTS_HEADER.split(",")
    assert payments["staging_model"] == "stg_collections__payments"


def test_tables_works_with_no_airflow(client: TestClient) -> None:
    """The picker must render before the pipeline profile is up.

    Asserted by the fixture: this client's Airflow is a closed port, and this
    still answers 200.
    """
    assert client.get("/api/ingest/tables").status_code == 200


# --- rejections, all of them before any handoff -------------------------------


def test_rejects_a_table_that_is_not_on_the_allow_list(client: TestClient) -> None:
    response = _post(client, table="analytics_marts.dim_customers")
    assert response.status_code == 422
    detail = response.json()["detail"]
    # Names the allowed tables: a rejection that does not say what *is* allowed
    # makes the user guess.
    assert "raw_payments" in detail


@pytest.mark.parametrize(
    "name",
    ["payments.json", "payments.xlsx", "payments", "payments.csv.exe"],
)
def test_rejects_unsupported_extensions(client: TestClient, name: str) -> None:
    response = _post(client, name=name)
    assert response.status_code == 422
    assert ".csv" in response.json()["detail"]


def test_header_mismatch_names_the_columns(client: TestClient) -> None:
    response = _post(
        client, body="payment_id,account_id,paid_on,amount,method\n9016,5001,x,1,card\n"
    )
    assert response.status_code == 422
    detail = response.json()["detail"]

    # A structured detail, like /api/chat's 422 — the UI shows the diff. The
    # useful half of "header mismatch" is *which* columns.
    assert detail["missing"] == ["payment_date"]
    assert detail["unexpected"] == ["paid_on"]
    assert "payment_date" in detail["message"]
    assert detail["expected"] == sorted(PAYMENTS_HEADER.split(","))


def test_header_check_ignores_column_order_and_case(client: TestClient) -> None:
    """A shuffled, upper-cased header is a valid file.

    The DAG names the columns in its COPY statement in the file's own order, and
    Postgres folded these names to lowercase when dbt created the table — so
    neither order nor case can be a reason to reject. This gets as far as the
    Airflow handoff, which is what the 503 proves.
    """
    shuffled = "METHOD,Amount,Payment_Date,Account_ID,PAYMENT_ID"
    response = _post(client, body=f"{shuffled}\ncard,1.00,2024-04-15,5001,9016\n")
    assert response.status_code == 503, response.text


def test_rejects_an_empty_file(client: TestClient) -> None:
    response = _post(client, body="")
    assert response.status_code == 422
    assert "empty" in response.json()["detail"].lower()


def test_rejects_a_file_over_the_size_limit(client: TestClient) -> None:
    # One row over 5 MB. 413 rather than 422: the request is well-formed, there
    # is simply too much of it.
    padding = "9016,5001,2024-04-15,210.00,card\n" * 200_000
    response = _post(client, body=f"{PAYMENTS_HEADER}\n{padding}")
    assert response.status_code == 413
    assert "5 MB" in response.json()["detail"]


def test_rejects_a_bad_delimiter_override(client: TestClient) -> None:
    response = _post(client, delimiter=";")
    assert response.status_code == 422
    assert "delimiter" in response.json()["detail"]


def test_delimiter_override_reinterprets_the_header(client: TestClient) -> None:
    """`.csv` infers a comma; the override wins, and the header check follows it.

    A comma-delimited file read as pipe-delimited has exactly one column — whose
    name is the whole header line — so this is a header mismatch, not a silent
    load of garbage.
    """
    response = _post(client, delimiter="|")
    assert response.status_code == 422
    assert response.json()["detail"]["found"] == [PAYMENTS_HEADER]


# --- the state the repo ships in: no orchestrator -----------------------------


def test_airflow_down_answers_503_with_the_fix(client: TestClient) -> None:
    response = _post(client)
    # 503, not 500: the API is fine, its orchestrator is not running, and the
    # fix belongs to whoever runs the stack. Same contract the catalog endpoints
    # use for "dbt hasn't run yet".
    assert response.status_code == 503
    detail = response.json()["detail"]
    # The Ingest page's empty state is driven by this text.
    assert "make pipeline-up" in detail


def test_run_status_of_an_unknown_run_is_503_when_airflow_is_down(
    client: TestClient,
) -> None:
    """Unreachable outranks not-found: we cannot know the run does not exist."""
    response = client.get("/api/ingest/runs/ingest__20260101T000000__deadbeef")
    assert response.status_code == 503


# --- the handoff itself, with a stubbed client --------------------------------


def test_hands_airflow_the_table_path_and_delimiter(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    """The `conf` is the whole API -> Airflow interface, so assert its shape.

    Stubbed rather than live: what is worth pinning down here is that the file
    lands on disk and only its *path* travels in the conf — a 5 MB CSV must never
    end up in Airflow's metadata database.
    """
    captured: dict = {}

    def fake_trigger(conf: dict, dag_run_id: str) -> dict:
        captured.update(conf)
        captured["_dag_run_id"] = dag_run_id
        return {"dag_run_id": "ingest__stub", "state": "queued"}

    monkeypatch.setattr(airflow_client, "trigger_file_ingest", fake_trigger)

    response = _post(client, name="../../etc/New Payments.CSV")
    assert response.status_code == 202, response.text
    body = response.json()

    assert body["dag_run_id"] == "ingest__stub"
    assert body["table"] == "raw_payments"
    assert body["poll"] == "/api/ingest/runs/ingest__stub"

    assert captured["table"] == "raw_payments"
    assert captured["delimiter"] == ","
    # Path only — never the bytes. (`_dag_run_id` is the fixture's own bookkeeping
    # from the separate argument, not part of the conf.)
    assert set(captured) == {"table", "file_path", "delimiter", "_dag_run_id"}
    assert captured["file_path"].startswith("/opt/airflow/uploads/")

    # The run id we asked Airflow for is URL-safe, and it is the same id the
    # staged filename is prefixed with — one id ties the file to the run.
    assert re.fullmatch(r"ingest__\d{8}T\d{6}__[0-9a-f]{8}", captured["_dag_run_id"])
    assert body["filename"].startswith(f"{captured['_dag_run_id']}__")

    # Traversal did not survive: the directories are gone, not escaped, and the
    # staged name is the one reported back to the client.
    staged = sorted((tmp_path / "uploads").iterdir())
    assert len(staged) == 1
    assert staged[0].read_bytes() == PAYMENTS_CSV.encode()
    assert staged[0].name.endswith("__new_payments.csv")
    assert "etc" not in staged[0].name
    assert staged[0].name == body["filename"]
    assert captured["file_path"].endswith(body["filename"])


def test_psv_extension_infers_the_pipe_delimiter(
    client: TestClient, monkeypatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        airflow_client,
        "trigger_file_ingest",
        lambda conf, dag_run_id: captured.update(conf) or {"dag_run_id": dag_run_id},
    )

    piped = PAYMENTS_HEADER.replace(",", "|")
    response = _post(
        client, name="new_payments.psv", body=f"{piped}\n9016|5001|2024-04-15|210.00|card\n"
    )
    assert response.status_code == 202, response.text
    assert captured["delimiter"] == "|"


# --- filename sanitisation, as a unit ----------------------------------------
#
# The client filename arrives in a multipart header that anyone can write by
# hand, and it is the one caller-supplied string this API joins to a filesystem
# path. Worth testing directly rather than only through the endpoint.


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param("payments.csv", "payments.csv", id="plain"),
        pytest.param("New Payments.CSV", "new_payments.csv", id="spaces-and-case"),
        pytest.param("../../etc/passwd", "passwd", id="posix-traversal"),
        pytest.param(r"C:\Windows\evil.csv", "evil.csv", id="windows-separators"),
        pytest.param("..", "upload", id="all-dots"),
        pytest.param("", "upload", id="empty"),
        pytest.param(None, "upload", id="absent-header"),
        pytest.param(".bashrc", "bashrc", id="leading-dot-stripped"),
        pytest.param("a" * 200 + ".csv", "a" * 80, id="truncated"),
        pytest.param("pay;rm -rf.csv", "pay_rm_-rf.csv", id="shell-metacharacters"),
        # Everything before the last separator is dropped, not escaped — so a
        # name whose "directory" is the interesting part loses it entirely.
        pytest.param("$(whoami)/x.csv", "x.csv", id="only-the-last-segment"),
    ],
)
def test_safe_filename(given: str | None, expected: str) -> None:
    assert _safe_filename(given) == expected


def test_safe_filename_never_escapes_a_directory(tmp_path) -> None:
    """The property that matters, stated as a property.

    Whatever comes in, joining the result to a directory has to stay inside it.
    """
    for hostile in ("../../../etc/passwd", "..", "/", "/etc/passwd", "a/../../b"):
        joined = (tmp_path / _safe_filename(hostile)).resolve()
        assert joined.parent == tmp_path.resolve(), hostile
