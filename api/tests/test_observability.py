"""Tracing, the observability endpoint, the token budget and the rate limit.

All of these need Postgres and **none of them needs an API key** — which is the
point. The control plane around an LLM feature (what did it cost, has the budget
gone, is one client hammering it) is testable without ever calling a model, so it
is tested unconditionally rather than behind a skip nobody exercises.

Where a test needs the endpoint to get *past* the is-configured gate, it patches
`llm.is_configured` rather than requiring a key. That is safe here precisely
because every one of those tests asserts the request is rejected *before* the LLM
call: an exhausted budget, an exceeded rate limit. Nothing in this file can
accidentally spend a token, with or without a key in the environment.

The rows are seeded with plain INSERTs. That is not a mock standing in for the
tracer: it is the same table the tracer writes to, and seeding lets the tests
assert the *aggregation* (today vs yesterday, tokens summed, budget arithmetic)
against inputs a real run would take a day to produce.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import llm, tracing
from app.config import get_settings
from app.db import engine
from app.main import app
from app.rate_limit import limiter

# Every row a test inserts is prefixed with this, so cleanup deletes exactly what
# the test made and a developer's own local traffic in the shared database is
# left alone.
MARKER = "[[pytest-trace]]"


def _database_reachable() -> bool:
    """Connectivity only — deliberately no DDL, so collecting the tests on a
    machine with no warehouse leaves nothing behind."""
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except SQLAlchemyError:
        return False
    return True


# One skip for the whole module: everything here reads or writes the trace table.
pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason="needs the local warehouse (`make local-up`)",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def rate_limit_off() -> Iterator[None]:
    """The limiter is off for every test except the one that tests it.

    Without this, the eleventh chat request in the session would 429 and the
    failure would land in whichever test happened to be eleventh — a suite whose
    result depends on test order.
    """
    limiter.enabled = False
    limiter.reset()
    yield
    limiter.enabled = False


@pytest.fixture(autouse=True)
def clean_traces() -> Iterator[None]:
    """Delete this test's rows before and after it runs.

    Before *and* after, so a previously crashed run cannot leak into this one's
    aggregate assertions.
    """
    _delete_marked()
    yield
    _delete_marked()


def _delete_marked() -> None:
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"delete from {tracing.TABLE} where question like :marker"),
                {"marker": f"%{MARKER}%"},
            )
    except SQLAlchemyError:
        # The table may not exist yet on a fresh database; nothing to clean.
        pass


def _seed(
    *,
    question: str = "seeded",
    tokens_prompt: int | None = 100,
    tokens_completion: int | None = 20,
    days_ago: int = 0,
    http_status: int = 200,
    answered: bool = True,
    source: str = "chat",
    guard_ok: bool | None = True,
    row_count: int | None = 3,
    latency_ms_total: int = 1234,
    error_class: str | None = None,
) -> None:
    tracing.ensure_table()
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                insert into {tracing.TABLE} (
                    ts, source, question, prompt_version, model,
                    tokens_prompt, tokens_completion, latency_ms_total,
                    guard_ok, row_count, answered, error_class, http_status
                ) values (
                    now() - make_interval(days => :days_ago),
                    :source, :question, 'test', 'test-model',
                    :tokens_prompt, :tokens_completion, :latency_ms_total,
                    :guard_ok, :row_count, :answered, :error_class, :http_status
                )
                """
            ),
            {
                "days_ago": days_ago,
                "source": source,
                "question": f"{MARKER} {question}",
                "tokens_prompt": tokens_prompt,
                "tokens_completion": tokens_completion,
                "latency_ms_total": latency_ms_total,
                "guard_ok": guard_ok,
                "row_count": row_count,
                "answered": answered,
                "error_class": error_class,
                "http_status": http_status,
            },
        )


def _marked(payload: dict) -> list[dict]:
    """Only the rows this test inserted — the local table may hold others."""
    return [row for row in payload["recent"] if MARKER in row["question"]]


def _row_count() -> int:
    tracing.ensure_table()
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"select count(*) from {tracing.TABLE}")).scalar()
        )


# --- the endpoint answers 200 whatever the state of the table ------------------


def test_observability_answers_200_when_nothing_is_traced(client: TestClient) -> None:
    """Zeros, not a 404 and not an error.

    A monitoring panel that goes red because nothing has happened yet trains
    people to ignore it. "No calls today" is an answer.
    """
    response = client.get("/api/observability/llm")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"today", "recent"}
    assert set(body["today"]) == {"calls", "tokens", "budget", "budget_used_pct"}
    # The budget is configuration, so it is reported even with no history at all.
    assert body["today"]["budget"] == get_settings().llm_daily_token_budget
    assert _marked(body) == []


def test_observability_aggregates_todays_calls(client: TestClient) -> None:
    before = client.get("/api/observability/llm").json()["today"]
    _seed(question="one", tokens_prompt=1000, tokens_completion=200)
    _seed(question="two", tokens_prompt=500, tokens_completion=100)
    after = client.get("/api/observability/llm").json()["today"]

    assert len(_marked(client.get("/api/observability/llm").json())) == 2
    # Deltas rather than absolutes: this test does not own the whole table, and a
    # developer's own local traffic may also be in today's numbers.
    assert after["calls"] - before["calls"] == 2
    assert after["tokens"] - before["tokens"] == 1800


def test_budget_used_pct_is_tokens_over_budget(client: TestClient) -> None:
    budget = get_settings().llm_daily_token_budget
    before = client.get("/api/observability/llm").json()["today"]
    # A tenth of the budget, so the percentage is a round number to assert on.
    _seed(question="a tenth", tokens_prompt=budget // 10, tokens_completion=0)
    after = client.get("/api/observability/llm").json()["today"]

    assert after["tokens"] == before["tokens"] + budget // 10
    assert after["budget_used_pct"] == pytest.approx(
        round(after["tokens"] * 100 / budget, 1)
    )


def test_observability_excludes_yesterday_from_today(client: TestClient) -> None:
    """The budget resets at midnight UTC, so yesterday's spend must not count."""
    before = client.get("/api/observability/llm").json()["today"]
    _seed(question="yesterday", tokens_prompt=9_000, tokens_completion=1_000, days_ago=1)
    body = client.get("/api/observability/llm").json()

    assert body["today"]["tokens"] == before["tokens"]
    assert body["today"]["calls"] == before["calls"]
    # ...but the row is still in `recent`, which is history, not today.
    assert any(row["question"].endswith("yesterday") for row in _marked(body))


def test_recent_rows_carry_the_operational_fields(client: TestClient) -> None:
    _seed(
        question="a failed one",
        tokens_prompt=None,
        tokens_completion=None,
        http_status=422,
        answered=False,
        guard_ok=False,
        row_count=None,
        error_class="UnsafeSql",
        latency_ms_total=999,
    )

    [row] = _marked(client.get("/api/observability/llm").json())
    assert row["http_status"] == 422
    assert row["guard_ok"] is False
    assert row["error_class"] == "UnsafeSql"
    assert row["row_count"] is None
    assert row["latency_ms_total"] == 999
    # Unknown tokens stay null. A confident 0 would be a claim about a call that
    # never reported its usage.
    assert row["tokens"] is None


def test_recent_is_newest_first_and_tags_the_source(client: TestClient) -> None:
    _seed(question="older", days_ago=1, source="eval")
    _seed(question="newest")

    marked = _marked(client.get("/api/observability/llm").json())
    assert [row["question"].removeprefix(f"{MARKER} ") for row in marked] == [
        "newest",
        "older",
    ]
    # Eval traffic is distinguishable from real traffic in the same table.
    assert marked[1]["source"] == "eval"
    assert marked[0]["source"] == "chat"


def test_long_questions_are_truncated_for_the_table(client: TestClient) -> None:
    _seed(question="x" * 400)
    [row] = _marked(client.get("/api/observability/llm").json())
    # QUESTION_PREVIEW characters at most, the last of which is the ellipsis.
    assert len(row["question"]) <= 90
    assert row["question"].endswith("…")


# --- the 503 path is deliberately untraced ------------------------------------


def test_a_missing_key_503s_without_writing_a_trace_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key means no call, and no call means nothing to trace.

    The check runs before the trace is even constructed. A row with no model, no
    tokens and no SQL would record a server that is unconfigured — a fact about
    deployment rather than an event in the LLM's history — and it would show up
    in the failure rate the observability panel reports.

    Patched rather than skipped so this always runs, key or no key.
    """
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    before = _row_count()

    response = client.post("/api/chat", json={"question": "does this get traced?"})

    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]
    assert _row_count() == before


# --- the daily token budget ---------------------------------------------------


def test_tokens_used_today_sums_prompt_and_completion() -> None:
    before = tracing.tokens_used_today()
    _seed(tokens_prompt=700, tokens_completion=300)
    assert tracing.tokens_used_today() == before + 1000


def test_budget_status_is_exhausted_at_the_limit_not_past_it() -> None:
    # >=, not >: reaching the budget must stop the next call, because the cost of
    # a call is not known until after it has been made.
    assert tracing.BudgetStatus(used=100, budget=100).exhausted
    assert not tracing.BudgetStatus(used=99, budget=100).exhausted


def test_chat_429s_when_the_budget_is_spent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed past the budget, then ask a question.

    The 429 fires before anything reaches the provider — which is the whole point
    of a budget, and why patching `is_configured` here cannot cost a token.
    """
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    budget = get_settings().llm_daily_token_budget
    _seed(question="expensive", tokens_prompt=budget, tokens_completion=1)

    # Marked, because this request writes its OWN trace row too (a 429 is an
    # event worth recording) and the cleanup fixture matches on the marker.
    response = client.post("/api/chat", json={"question": f"{MARKER} how many open cases?"})
    assert response.status_code == 429

    detail = response.json()["detail"]
    assert "daily LLM token budget" in detail
    assert "midnight UTC" in detail
    # The figures are in the message, so an operator can see how far past the
    # line they are without opening the database.
    assert f"{budget:,}" in detail


def test_the_budget_429_is_itself_traced(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected request is an event worth seeing.

    Throttling that leaves no trace is indistinguishable from a quiet day.
    """
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    _seed(
        question="expensive",
        tokens_prompt=get_settings().llm_daily_token_budget,
        tokens_completion=0,
    )
    question = f"{MARKER} traced 429 please"

    client.post("/api/chat", json={"question": question})

    with engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                select http_status, error_class, answered, source, tokens_prompt
                from {tracing.TABLE}
                where question = :question
                order by id desc limit 1
                """
            ),
            {"question": question},
        ).one_or_none()

    assert row is not None, "the 429 should have been traced"
    assert (row.http_status, row.error_class, row.answered, row.source) == (
        429,
        "BudgetExhausted",
        False,
        "chat",
    )
    # No tokens on a call that never happened, so a wall of 429s cannot inflate
    # the very number that produced them.
    assert row.tokens_prompt is None


# --- the per-IP rate limit ----------------------------------------------------


def test_chat_rate_limits_one_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """slowapi keys on `request.client.host`, which TestClient sets, so this is
    testable in-process — no curl loop needed.

    `is_configured` is patched *off* so every request short-circuits to 503 in
    microseconds: the assertion is about the limiter being wired to this route
    and answering in our error shape, and no LLM call is needed (or wanted) to
    show that.
    """
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()

    allowed = int(get_settings().chat_rate_limit.split("/")[0])
    statuses = [
        client.post("/api/chat", json={"question": f"q{index}"}).status_code
        for index in range(allowed + 1)
    ]

    assert statuses[:allowed] == [503] * allowed, statuses
    assert statuses[-1] == 429, statuses

    # Our `{"detail": ...}` shape, not slowapi's `{"error": ...}` — the web
    # client reads `detail` for every other error and must not need a second path.
    detail = client.post("/api/chat", json={"question": "one more"}).json()["detail"]
    assert "Too many questions" in detail
    limiter.reset()
