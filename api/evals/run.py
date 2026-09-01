"""The eval runner: `uv run python -m evals.run` (or `make eval` from the root).

Measures the NL->SQL feature the only way that means anything — end to end,
through the code the endpoint actually runs (`app.nl2sql`), against the real
warehouse, scored on result sets rather than SQL text.

Per question, four things can go wrong and they are reported separately because
they have different fixes:

    valid_sql       the model wrote SQL the guard accepted   -> prompt problem
    executed        the warehouse accepted it                -> schema problem
    results_match   it answered the question                 -> model/reasoning
    latency/tokens  what it cost                             -> config problem

**Two modes, and it says which, loudly.** With a GROQ_API_KEY it runs the whole
pipeline. Without one it runs in *reference-check* mode: every `reference_sql` in
the golden file is still validated and executed, and the LLM leg is reported as
skipped. That is not a degraded no-op — a broken golden file is the failure mode
that makes every future eval meaningless, and this catches it on a laptop with no
key and in CI with no secret. What it must never do is print a green summary that
looks like the model passed.

Rows land in `platform_ops.llm_calls` with `source='eval'`, so eval traffic is
visible in the same observability endpoint as live traffic but separable from it.
The runner does not enforce the daily token budget (it is an operator tool, not a
public endpoint) but its tokens *count against* it, because they are real tokens
on the same key.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.exc import SQLAlchemyError

from app import llm, nl2sql, tracing
from app.db import engine
from app.schema_context import build_schema_context
from app.sql_guard import UnsafeSql, validate

from .compare import Comparison, compare_results

# Resolved from this file, not the cwd: `make eval` runs from the repo root and
# a developer runs it from api/, and the golden set is in neither place.
EVALS_DIR = Path(__file__).resolve().parent
GOLDEN_FILE = EVALS_DIR / "golden.yaml"
RESULTS_DIR = EVALS_DIR / "results"


@dataclass(frozen=True, slots=True)
class GoldenEntry:
    id: str
    question: str
    reference_sql: str


@dataclass
class QuestionResult:
    """One row of the report. Field order is the order it reads on screen."""

    id: str
    question: str
    # The reference leg. Always attempted, in both modes.
    reference_ok: bool = False
    reference_error: str | None = None
    reference_row_count: int | None = None
    # The LLM leg. All None in reference-check mode.
    valid_sql: bool | None = None
    executed: bool | None = None
    results_match: bool | None = None
    mismatch_reason: str | None = None
    generated_sql: str | None = None
    guard_error: str | None = None
    execution_error: str | None = None
    row_count: int | None = None
    attempts: int | None = None
    latency_ms_llm: int | None = None
    latency_ms_sql: int | None = None
    tokens: int = 0


@dataclass
class Summary:
    mode: str
    prompt_version: str
    model: str | None
    questions: int = 0
    references_ok: int = 0
    valid_sql: int = 0
    executed: int = 0
    matched: int = 0
    total_tokens: int = 0
    wall_seconds: float = 0.0
    results: list[QuestionResult] = field(default_factory=list)

    def _rate(self, count: int) -> float | None:
        """Percentage of questions, or None when the LLM leg never ran.

        None rather than 0.0: "the model scored zero" and "the model was not
        asked" must not render as the same number.
        """
        if self.mode != "live" or self.questions == 0:
            return None
        return round(count * 100 / self.questions, 1)

    @property
    def valid_sql_pct(self) -> float | None:
        return self._rate(self.valid_sql)

    @property
    def execution_pct(self) -> float | None:
        return self._rate(self.executed)

    @property
    def accuracy_pct(self) -> float | None:
        """Execution accuracy: matched / questions.

        Denominator is every question, not just the ones that executed. A
        question the model could not write SQL for is a question it got wrong.
        """
        return self._rate(self.matched)


def load_golden(path: Path = GOLDEN_FILE) -> list[GoldenEntry]:
    """Parse golden.yaml, failing loudly on anything malformed.

    `safe_load`, and every field checked: this file is meant to be edited by hand
    (that is the exercise), so a typo must produce a message naming the entry
    rather than a KeyError three functions later.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{path} must contain a non-empty YAML list of entries")

    entries: list[GoldenEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"{path}: entry {index} is not a mapping")
        missing = {"id", "question", "reference_sql"} - set(item)
        if missing:
            raise SystemExit(
                f"{path}: entry {index} is missing {', '.join(sorted(missing))}"
            )
        entry = GoldenEntry(
            id=str(item["id"]),
            question=str(item["question"]).strip(),
            reference_sql=str(item["reference_sql"]).strip(),
        )
        # Duplicate ids would silently overwrite each other in the report.
        if entry.id in seen:
            raise SystemExit(f"{path}: duplicate id {entry.id!r}")
        seen.add(entry.id)
        entries.append(entry)
    return entries


def _run_sql(sql: str) -> nl2sql.Execution:
    """Execute one statement with the same guarantees the endpoint gives.

    Its own connection per query rather than one for the whole run: `nl2sql.
    execute` opens an explicit read-only transaction, and a failed statement
    leaves the connection needing a rollback. One connection per query keeps a
    broken reference from poisoning the queries after it.
    """
    with engine.connect() as connection:
        return nl2sql.execute(connection, sql)


def _check_reference(entry: GoldenEntry, result: QuestionResult) -> nl2sql.Execution | None:
    """Validate + execute the reference SQL. Returns None if it is broken.

    The reference goes through the *same guard* as the model's SQL. That is not
    ceremony: it means a reference with a stray second statement or a missing
    LIMIT is caught here, and it guarantees both sides of the comparison ran
    under identical rules — including the 100-row cap, which would otherwise make
    a large reference disagree with a capped generation for no good reason.
    """
    try:
        safe_sql = validate(entry.reference_sql)
    except UnsafeSql as error:
        result.reference_error = f"reference SQL rejected by the guard: {error}"
        return None
    try:
        execution = _run_sql(safe_sql)
    except SQLAlchemyError as exc:
        result.reference_error = str(getattr(exc, "orig", exc)).strip()
        return None
    result.reference_ok = True
    result.reference_row_count = execution.row_count
    return execution


def _run_llm_leg(
    entry: GoldenEntry,
    schema: str,
    result: QuestionResult,
    reference: nl2sql.Execution | None,
) -> None:
    """Generate, guard, execute, compare — and trace the call.

    Mutates `result` in place so a failure at any stage leaves the stages before
    it recorded. The trace row is written in a `finally` for the same reason the
    endpoint does it: a call that cost tokens must appear in the budget even if
    the comparison after it blew up.
    """
    trace = tracing.LlmCallTrace(
        question=entry.question, model=llm.model_name(), source="eval"
    )
    started = time.perf_counter()
    try:
        generation = nl2sql.generate_validated_sql(entry.question, schema)
        result.valid_sql = generation.valid
        result.generated_sql = generation.safe_sql or generation.sql
        result.guard_error = generation.guard_error
        result.attempts = generation.attempts
        result.latency_ms_llm = generation.latency_ms
        result.tokens = (generation.tokens_prompt or 0) + (generation.tokens_completion or 0)

        trace.tokens_prompt = generation.tokens_prompt
        trace.tokens_completion = generation.tokens_completion
        trace.latency_ms_llm = generation.latency_ms
        trace.sql_text = result.generated_sql
        trace.guard_ok = generation.valid
        trace.guard_error = generation.guard_error

        if generation.safe_sql is None:
            result.executed = False
            result.results_match = False
            trace.http_status = 422
            trace.error_class = "UnsafeSql"
            return

        try:
            execution = _run_sql(generation.safe_sql)
        except SQLAlchemyError as exc:
            result.executed = False
            result.results_match = False
            result.execution_error = str(getattr(exc, "orig", exc)).strip()
            trace.http_status = 422
            trace.error_class = "WarehouseRejected"
            return

        result.executed = True
        result.row_count = execution.row_count
        result.latency_ms_sql = execution.latency_ms
        trace.latency_ms_sql = execution.latency_ms
        trace.row_count = execution.row_count
        trace.answered = True
        trace.http_status = 200

        if reference is None:
            # The model may well have answered correctly; we have nothing to
            # score it against, so it is unscored rather than wrong.
            result.mismatch_reason = "reference SQL failed, so nothing to compare against"
            return

        comparison: Comparison = compare_results(
            execution.columns, execution.rows, reference.columns, reference.rows
        )
        result.results_match = comparison.match
        result.mismatch_reason = comparison.reason
    except llm.LlmError as error:
        # A provider failure (throttled, timed out, key rejected) is not the
        # model being wrong, so valid_sql stays False and the reason says why.
        result.valid_sql = False
        result.executed = False
        result.results_match = False
        result.mismatch_reason = f"LLM call failed: {error}"
        trace.http_status = getattr(error, "status_code", 502)
        trace.error_class = type(error).__name__
    finally:
        trace.latency_ms_total = round((time.perf_counter() - started) * 1000)
        tracing.record(trace)


# --- reporting ---------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value}%"


def _relative(path: Path) -> str:
    """A path relative to the cwd when it is under it, absolute otherwise.

    Cosmetic, but the summary is read in a terminal: `evals/results/....json` is
    a path you can paste, a 90-character absolute one wraps and is not.
    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _flag(value: bool | None) -> str:
    return {True: "yes", False: "NO", None: "–"}[value]


def _print_question(index: int, total: int, result: QuestionResult, mode: str) -> None:
    print(f"[{index}/{total}] {result.id}")
    print(f"        {result.question}")
    if result.reference_ok:
        count = result.reference_row_count
        print(f"        reference: ok ({count} row{'' if count == 1 else 's'})")
    else:
        print(f"        reference: FAILED — {result.reference_error}")

    if mode != "live":
        print("        generated: skipped (no LLM)")
        return

    print(
        f"        generated: valid_sql={_flag(result.valid_sql)} "
        f"executed={_flag(result.executed)} "
        f"results_match={_flag(result.results_match)}"
    )
    detail = [
        f"llm {result.latency_ms_llm}ms" if result.latency_ms_llm is not None else None,
        f"sql {result.latency_ms_sql}ms" if result.latency_ms_sql is not None else None,
        f"{result.tokens} tokens" if result.tokens else None,
        f"{result.attempts} attempts" if result.attempts and result.attempts > 1 else None,
    ]
    print("        " + "  ".join(part for part in detail if part))
    for label, message in (
        ("guard", result.guard_error),
        ("warehouse", result.execution_error),
        ("mismatch", result.mismatch_reason),
    ):
        if message:
            print(f"        {label}: {message}")


def _print_summary(summary: Summary, report_path: Path) -> None:
    if summary.mode == "live":
        banner = [f"LIVE mode — the full pipeline ran against {summary.model}."]
    else:
        # Three lines, shouted, at the top of the summary. The failure this
        # guards against is someone skimming a green run and believing the model
        # was measured.
        banner = [
            "REFERENCE-CHECK mode — no GROQ_API_KEY was set, so",
            "*** THE LLM LEG WAS SKIPPED ***",
            "Only the golden file's reference SQL ran. Nothing below says",
            "anything about model accuracy.",
        ]

    print()
    print("=" * 78)
    for line in banner:
        print(f"  {line}")
    print("=" * 78)
    rows = [
        ("questions", str(summary.questions)),
        ("reference SQL ok", f"{summary.references_ok}/{summary.questions}"),
        ("valid-SQL rate", _pct(summary.valid_sql_pct)),
        ("execution success", _pct(summary.execution_pct)),
        ("execution accuracy", _pct(summary.accuracy_pct)),
        ("total tokens", f"{summary.total_tokens:,}"),
        ("wall time", f"{summary.wall_seconds:.2f}s"),
        ("prompt version", summary.prompt_version),
        ("report", _relative(report_path)),
    ]
    for label, value in rows:
        print(f"  {label:<20} {value}")
    print()


def write_report(summary: Summary, started_at: dt.datetime) -> Path:
    """Persist the run as JSON, one file per run, named by UTC timestamp.

    A file per run rather than an appended log: these are compared *between*
    runs (prompt A vs prompt B, model A vs model B), and one self-contained
    document per run is the shape that makes `diff` and `jq` useful. `results/`
    is gitignored — they are outputs, and a committed one would be stale by the
    next commit.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # Colons are legal on POSIX but not on Windows, and awkward in shell
    # arguments everywhere.
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    path = RESULTS_DIR / f"{stamp}.json"
    payload: dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "mode": summary.mode,
        "prompt_version": summary.prompt_version,
        "model": summary.model,
        "summary": {
            "questions": summary.questions,
            "references_ok": summary.references_ok,
            "valid_sql": summary.valid_sql,
            "valid_sql_pct": summary.valid_sql_pct,
            "executed": summary.executed,
            "execution_pct": summary.execution_pct,
            "matched": summary.matched,
            "accuracy_pct": summary.accuracy_pct,
            "total_tokens": summary.total_tokens,
            "wall_seconds": round(summary.wall_seconds, 3),
        },
        "results": [asdict(result) for result in summary.results],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


# --- entry point -------------------------------------------------------------


def run(golden_path: Path = GOLDEN_FILE) -> Summary:
    entries = load_golden(golden_path)
    live = llm.is_configured()
    summary = Summary(
        mode="live" if live else "reference-check",
        prompt_version=nl2sql.PROMPT_VERSION,
        model=llm.model_name() if live else None,
        questions=len(entries),
    )

    # Built once, outside the loop, exactly as the endpoint's cache would serve
    # it — so the briefing is identical for every question and its build time is
    # not charged to question 1's latency.
    schema = build_schema_context() if live else ""

    print(f"NL->SQL evals · {len(entries)} questions · mode: {summary.mode}")
    if not live:
        print("  (no GROQ_API_KEY: checking the golden file's reference SQL only)")
    print()

    started = time.perf_counter()
    for index, entry in enumerate(entries, start=1):
        result = QuestionResult(id=entry.id, question=entry.question)
        reference = _check_reference(entry, result)
        if live:
            _run_llm_leg(entry, schema, result, reference)

        summary.results.append(result)
        summary.references_ok += int(result.reference_ok)
        summary.valid_sql += int(bool(result.valid_sql))
        summary.executed += int(bool(result.executed))
        summary.matched += int(bool(result.results_match))
        summary.total_tokens += result.tokens
        _print_question(index, len(entries), result, summary.mode)
        print()

    summary.wall_seconds = time.perf_counter() - started
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description=(
            "Run the NL->SQL eval suite over api/evals/golden.yaml. Without a "
            "GROQ_API_KEY it runs in reference-check mode: the golden file's SQL "
            "is validated and executed, and the LLM leg is skipped."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=GOLDEN_FILE,
        help="path to the golden set (default: evals/golden.yaml)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        metavar="PCT",
        help=(
            "exit 1 if execution accuracy is below this percentage. For CI. "
            "Ignored in reference-check mode, where there is no accuracy to "
            "compare — a broken reference still fails the run."
        ),
    )
    args = parser.parse_args(argv)

    started_at = dt.datetime.now(dt.UTC)
    summary = run(args.golden)
    report_path = write_report(summary, started_at)
    _print_summary(summary, report_path)

    # A broken golden file fails the run in BOTH modes. This is the whole reason
    # reference-check mode exists: without it, CI with no API key would print a
    # green summary over a golden set whose SQL no longer runs.
    if summary.references_ok < summary.questions:
        broken = [r.id for r in summary.results if not r.reference_ok]
        print(f"FAIL: reference SQL is broken for {', '.join(broken)}", file=sys.stderr)
        return 1

    if args.threshold is not None:
        accuracy = summary.accuracy_pct
        if accuracy is None:
            print(
                "NOTE: --threshold was given but the LLM leg did not run, so "
                "accuracy was not measured. Set GROQ_API_KEY to enforce it.",
                file=sys.stderr,
            )
        elif accuracy < args.threshold:
            print(
                f"FAIL: execution accuracy {accuracy}% is below the "
                f"{args.threshold}% threshold",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
