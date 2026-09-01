"""The eval runner's orchestration, with a stubbed provider.

This is the one place in the suite that stubs the LLM, and the exception is
deliberate. Everywhere else a mocked model would only prove that our code passes
our own fake SQL through (see tests/test_chat.py). Here the *runner* is the thing
under test, not the model: does a correct answer score as correct, does a wrong
one score as wrong and say why, does live mode reach the warehouse, and do the
calls land in the trace table tagged `source='eval'`. None of that is about which
SQL the stub returns, and all of it is otherwise unverified without an API key —
which is exactly the situation this repo ships in.

Reference-check mode is tested the same way but needs no stub at all.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import llm, tracing
from app.db import engine
from evals import run as runner

STUB_MODEL = "stub-model-for-tests"

# Correct SQL for three golden questions, written with *different aliases* than
# the reference on purpose — the point is that the comparison ignores names.
CORRECT_SQL = {
    "Which team has the highest cure rate?": """
        select team, round(sum(cured_cases) * 100.0 / nullif(sum(case_count), 0), 1) as r
        from analytics_marts.collections_performance
        group by team order by r desc limit 1
    """,
    "What is the total delinquent amount in each delinquency bucket?": """
        select delinquency_bucket as b, sum(delinquent_amount) as amt
        from analytics_marts.fct_collection_cases group by 1
    """,
    "How many collection cases are currently open?": """
        select count(*) from analytics_marts.fct_collection_cases
        where case_status = 'open'
    """,
    # Deliberately wrong: an extra column. Correct figures, different question.
    "Which customer has the largest total balance?": """
        select full_name, total_balance, state from analytics_marts.dim_customers
        order by total_balance desc limit 1
    """,
}


def _database_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(
                text("select 1 from analytics_marts.fct_collection_cases limit 1")
            )
    except SQLAlchemyError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason="needs the local marts (`make local-up && make local-build`)",
)


@pytest.fixture(autouse=True)
def clean_stub_traces() -> Iterator[None]:
    """Remove the rows this file's stub wrote, identified by its model name."""
    yield
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"delete from {tracing.TABLE} where model = :model"),
                {"model": STUB_MODEL},
            )
    except SQLAlchemyError:
        pass


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the pipeline think it has a key, and answer from CORRECT_SQL.

    Patched on `app.llm` rather than on `app.nl2sql`, because nl2sql calls
    `llm.complete(...)` as a module attribute — so this covers the real call site
    instead of a re-export.
    """

    def complete(system: str, user: str, temperature: float = 0.0) -> llm.Completion:
        if "SQL analyst" not in system:
            # The summarising call. The runner does not ask for prose, so this is
            # only reached if that ever changes.
            return llm.Completion(text="Prose.", tokens_prompt=300, tokens_completion=30)
        question = user.split("Question: ", 1)[1].split("\n")[0]
        return llm.Completion(
            text=CORRECT_SQL[question], tokens_prompt=1200, tokens_completion=40
        )

    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: STUB_MODEL)


# --- reference-check mode: no key, no stub ------------------------------------


def test_reference_check_mode_runs_every_reference_and_skips_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "is_configured", lambda: False)

    summary = runner.run()

    assert summary.mode == "reference-check"
    assert summary.model is None
    # The committed golden set: four questions whose reference SQL must run.
    assert summary.questions == 4
    assert summary.references_ok == 4
    assert summary.total_tokens == 0
    # None, not 0.0 — "the model scored zero" and "the model was not asked" must
    # not render as the same number.
    assert summary.accuracy_pct is None
    assert summary.valid_sql_pct is None
    assert all(result.valid_sql is None for result in summary.results)


def test_the_committed_golden_file_parses_and_is_unique() -> None:
    entries = runner.load_golden()
    assert len(entries) == 4
    assert len({entry.id for entry in entries}) == len(entries)
    assert all(entry.question and entry.reference_sql for entry in entries)


def test_a_broken_golden_file_fails_the_run(tmp_path) -> None:
    """The whole reason reference-check mode exists.

    Without this, CI with no API key would print a green summary over a golden
    set whose SQL no longer runs — and every future eval would be meaningless.
    """
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        "- id: typo\n"
        "  question: Anything?\n"
        "  reference_sql: select nonexistent_column from analytics_marts.dim_agents\n"
    )
    assert runner.main(["--golden", str(broken)]) == 1


def test_a_malformed_golden_file_is_rejected_by_name(tmp_path) -> None:
    """It is meant to be hand-edited, so a typo must name the entry."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: no_question\n  reference_sql: select 1\n")
    with pytest.raises(SystemExit, match="missing question"):
        runner.load_golden(bad)


# --- live mode ----------------------------------------------------------------


def test_live_mode_scores_correct_sql_as_correct(stub_llm: None) -> None:
    summary = runner.run()

    assert summary.mode == "live"
    assert summary.model == STUB_MODEL
    assert summary.valid_sql == 4
    assert summary.executed == 4
    # Three of the four stubs are correct; the fourth returns an extra column.
    assert summary.matched == 3
    assert summary.accuracy_pct == 75.0
    assert summary.valid_sql_pct == 100.0
    # Two attempts were never needed, so one SQL call per question.
    assert summary.total_tokens == 4 * 1240


def test_differing_aliases_do_not_cost_accuracy(stub_llm: None) -> None:
    """The stub aliases its columns `r`, `b`, `amt` — nothing like the reference."""
    summary = runner.run()
    by_id = {result.id: result for result in summary.results}

    assert by_id["highest_cure_rate_team"].results_match is True
    assert by_id["delinquent_amount_by_bucket"].results_match is True


def test_an_arity_mismatch_is_reported_with_a_reason(stub_llm: None) -> None:
    summary = runner.run()
    wrong = next(r for r in summary.results if r.id == "largest_balance_customer")

    assert wrong.executed is True
    assert wrong.results_match is False
    assert "column count differs" in wrong.mismatch_reason
    # The generated SQL is kept, so the report shows what it actually ran.
    assert "state" in wrong.generated_sql


def test_live_mode_traces_every_call_as_eval_source(stub_llm: None) -> None:
    runner.run()

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"""
                select source, model, prompt_version, tokens_prompt, guard_ok,
                       answered, http_status
                from {tracing.TABLE}
                where model = :model
                """
            ),
            {"model": STUB_MODEL},
        ).all()

    assert len(rows) == 4
    # Eval traffic must be separable from live traffic in the same table, or the
    # observability panel's averages would be a blend of a batch and a trickle.
    assert {row.source for row in rows} == {"eval"}
    assert {row.answered for row in rows} == {True}
    assert {row.http_status for row in rows} == {200}
    assert all(row.tokens_prompt == 1200 for row in rows)


def test_a_provider_failure_is_not_scored_as_a_wrong_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Throttled is not the same as wrong, and the report has to say which."""
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: STUB_MODEL)

    def throttled(system: str, user: str, temperature: float = 0.0) -> llm.Completion:
        raise llm.LlmRateLimited()

    monkeypatch.setattr(llm, "complete", throttled)

    summary = runner.run()

    assert summary.matched == 0
    assert summary.valid_sql == 0
    # The references still ran, so a broken golden file is still distinguishable
    # from a broken provider.
    assert summary.references_ok == 4
    assert all("LLM call failed" in result.mismatch_reason for result in summary.results)

    with engine.connect() as connection:
        statuses = connection.execute(
            text(f"select http_status, error_class from {tracing.TABLE} where model = :m"),
            {"m": STUB_MODEL},
        ).all()
    assert {row.http_status for row in statuses} == {429}
    assert {row.error_class for row in statuses} == {"LlmRateLimited"}
