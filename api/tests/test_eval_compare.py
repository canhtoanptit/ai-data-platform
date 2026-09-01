"""Unit tests for the eval harness's scoring rules (evals/compare.py).

These are the harness's own tests, and they matter more than they look: every
number the eval runner reports is this function's opinion. A comparison that is
too strict makes a correct model look broken (and someone "fixes" the prompt); a
comparison that is too loose makes a broken model look correct, which is worse
because nothing tells you.

No warehouse, no API key, no LLM — pure functions over lists of rows, so these
never skip. Same bargain as tests/test_sql_guard.py.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from evals.compare import cells_equal, compare_results, normalize_value

# Shorthand: the reference side of every comparison below.
COLS = ["a", "b"]


def match(
    generated_rows: list[list[object]],
    reference_rows: list[list[object]],
    generated_columns: list[str] | None = None,
    reference_columns: list[str] | None = None,
) -> bool:
    return compare_results(
        generated_columns or COLS,
        generated_rows,
        reference_columns or COLS,
        reference_rows,
    ).match


# --- row order is not part of the answer --------------------------------------


def test_row_order_is_ignored() -> None:
    reference = [["early_stage", 1], ["late_stage", 2]]
    assert match([["late_stage", 2], ["early_stage", 1]], reference)


def test_duplicate_rows_are_counted_not_collapsed() -> None:
    """Multiset, not set. Two identical rows are two answers.

    This is the test that catches a wrong `group by`: a query returning a row
    per case where the reference returns a row per team would pass a
    set-comparison whenever the values happened to coincide.
    """
    assert not match([["x", 1]], [["x", 1], ["x", 1]])
    assert match([["x", 1], ["x", 1]], [["x", 1], ["x", 1]])


def test_row_count_mismatch_says_so() -> None:
    result = compare_results(COLS, [["x", 1]], COLS, [["x", 1], ["y", 2]])
    assert not result.match
    assert "row count differs" in result.reason
    assert "1 vs 2" in result.reason


# --- Decimal vs float: the same figure in two representations -----------------


def test_decimal_and_float_are_the_same_number() -> None:
    """Postgres returns Decimal for `numeric` and float for a division.

    Two correct queries can therefore disagree on type while agreeing on the
    figure, and that must not be scored as wrong.
    """
    assert match([["x", 1490.20]], [["x", Decimal("1490.20")]])
    assert match([["x", Decimal("50.0")]], [["x", 50.0]])


def test_float_noise_within_tolerance_matches() -> None:
    assert match([["x", 50.0]], [["x", 50.0 + 1e-12]])


def test_a_real_numeric_difference_does_not_match() -> None:
    result = compare_results(COLS, [["x", 50.0]], COLS, [["x", 50.1]])
    assert not result.match
    assert "50.0" in result.reason and "50.1" in result.reason


def test_tolerance_does_not_swallow_a_visible_difference() -> None:
    # 1e-6 is far larger than the 1e-9 tolerance: still a mismatch.
    assert not match([["x", 1.0]], [["x", 1.000001]])


def test_int_and_float_of_the_same_value_match() -> None:
    # `count(*)` is a bigint; `sum(...)/1` may come back as a float.
    assert match([["x", 4]], [["x", 4.0]])


# --- dates normalise to ISO strings ------------------------------------------


def test_date_matches_its_isoformat_string() -> None:
    """A `date` column and the same date cast to text are the same day.

    The model often writes `to_char(...)` or `::text` where the reference selects
    the raw column; that is presentation, not a different answer.
    """
    assert match([["x", "2026-08-01"]], [["x", dt.date(2026, 8, 1)]])


def test_datetime_and_date_are_not_confused() -> None:
    """datetime is a date subclass in Python; their isoformats differ, as they should."""
    assert normalize_value(dt.datetime(2026, 8, 1, 12, 30)) == "2026-08-01T12:30:00"
    assert normalize_value(dt.date(2026, 8, 1)) == "2026-08-01"
    assert not match([["x", dt.datetime(2026, 8, 1)]], [["x", dt.date(2026, 8, 1)]])


# --- NULL is its own thing ---------------------------------------------------


@pytest.mark.parametrize("other", [0, 0.0, "", False, "None"])
def test_null_equals_only_null(other: object) -> None:
    """"Unknown" and "zero" are different answers.

    Every rate in these marts is null rather than 0 when its denominator is
    empty, so a comparison that treated them as equal would score a query with a
    missing `nullif` as correct.
    """
    assert not cells_equal(None, other)
    assert not cells_equal(other, None)
    assert cells_equal(None, None)


def test_true_does_not_equal_one() -> None:
    """`is_delinquent` is boolean; `is_cured` is a 0/1 int. Not interchangeable."""
    assert not cells_equal(True, 1)
    assert not cells_equal(1, True)
    assert cells_equal(True, True)


# --- aliases are free, arity is not -------------------------------------------


def test_column_names_may_differ() -> None:
    """The model's alias is its own choice: `total_amount` vs `sum` is not an error."""
    assert match(
        [["1-30 dpd", 1490.20]],
        [["1-30 dpd", Decimal("1490.20")]],
        generated_columns=["bucket", "total_amount"],
        reference_columns=["delinquency_bucket", "sum"],
    )


def test_column_count_must_match() -> None:
    """Arity is not a naming choice — an extra column is a different question."""
    result = compare_results(
        ["team", "cure_rate", "case_count"],
        [["early_stage", 50.0, 4]],
        ["team", "cure_rate"],
        [["early_stage", 50.0]],
    )
    assert not result.match
    assert "column count differs" in result.reason
    assert "3" in result.reason and "2" in result.reason


def test_fewer_columns_also_fails() -> None:
    assert not match(
        [["early_stage"]],
        [["early_stage", 50.0]],
        generated_columns=["team"],
        reference_columns=["team", "cure_rate"],
    )


# --- the shapes the golden set actually produces ------------------------------


def test_a_scalar_result_matches() -> None:
    assert match([[4]], [[4]], generated_columns=["n"], reference_columns=["open_cases"])


def test_empty_results_match_each_other() -> None:
    """Zero rows is a legitimate answer, and two empty results agree."""
    assert match([], [])


def test_empty_does_not_match_populated() -> None:
    assert not match([], [["x", 1]])


def test_mixed_types_in_one_column_still_sort() -> None:
    """A column of `text` with NULLs, in the other order. Must not raise.

    Python refuses to compare None with a str, so the sort key has to bucket by
    kind — this is the test that would catch that regressing.
    """
    assert match([["b", 1], [None, 2]], [[None, 2], ["b", 1]])


def test_tuples_and_lists_are_both_acceptable_rows() -> None:
    """SQLAlchemy hands back Row objects; the runner passes lists. Accept both."""
    assert match([("x", 1)], [["x", 1]])
