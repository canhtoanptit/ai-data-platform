"""Does the model's result set say the same thing as the reference's?

This is the scoring function of the whole harness, and every rule in it is a
judgement about what "correct" means for text-to-SQL. Stated plainly:

**Compare results, never SQL text.** There are a dozen correct ways to write
"total delinquent amount by bucket" — `group by 1`, a CTE, a window function,
different join order. Grading on string similarity would fail all but the one we
happened to write down, which measures our taste, not the model's accuracy.

**Row order is ignored.** Unless the question asked for an order ("the top
team"), the order rows come back in is an artifact of the plan. Both sides are
therefore compared as *unordered multisets* — multi*sets*, not sets, because
duplicate rows are data: two customers with the same balance are two answers,
and collapsing them would hide a wrong `group by`.

**Column names are ignored; column count is not.** An alias is the model's
choice — `total_amount`, `sum`, `sum_delinquent` are the same answer wearing
different labels, and holding it to our naming would fail correct SQL. Arity is
not a choice: a query that returns three columns where the question asked for two
answered a different question, and a query returning one where two were asked has
dropped something. So names are dropped and `len(columns)` is checked.

**Type differences that are representation, not meaning, are normalised.**
Postgres returns `Decimal` for `numeric` and `float` for a division; a `date`
column and a `date` cast to text are the same day. Neither difference is the
model getting the answer wrong, so Decimals become floats (compared with a
1e-9 tolerance) and dates/datetimes become ISO strings. NULL stays NULL and only
ever equals NULL — "unknown" and "zero" are different answers, and every KPI in
this project depends on that distinction.

Pure functions over lists of rows: no database, no LLM, no config. Which is why
this module carries the harness's unit tests (tests/test_eval_compare.py) and
they never skip.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

Row = list[Any] | tuple[Any, ...]

# Absolute, not relative. The differences this absorbs are representation-level
# (Decimal('50.0') vs 50.0, or the last bit of a float division), never a
# genuinely different figure — a real disagreement in a money or rate column is
# orders of magnitude larger than this.
TOLERANCE = 1e-9


def normalize_value(value: Any) -> Any:
    """One cell, reduced to a comparable form.

    Ordered deliberately: `bool` before `int` (bool is an int subclass in Python,
    and `True == 1` is not a comparison we want to make), `Decimal` before the
    numeric passthrough, `datetime` before `date` (datetime is a date subclass,
    and `.isoformat()` differs between them). Anything unrecognised is
    stringified rather than raising — the SQL is model-written, so an exotic
    column type must degrade to something comparable, not crash the run.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def normalize_rows(rows: list[Row]) -> list[tuple[Any, ...]]:
    return [tuple(normalize_value(cell) for cell in row) for row in rows]


def _cell_sort_key(value: Any) -> tuple[int, float, str]:
    """A total order over normalised cells, so two row lists can be aligned.

    A fixed-width triple because Python will not compare `None` with `3` or `3`
    with `"a"`, and a result set may legitimately contain all three. The first
    element buckets by kind; only one of the remaining two is meaningful per
    kind, and within a kind the comparison is homogeneous.

    This is a *sort* key, not an equality key: near-equal floats land adjacent so
    the tolerance below still gets to decide whether they match.
    """
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, (int, float)):
        return (2, float(value), "")
    return (3, 0.0, str(value))


def _row_sort_key(row: tuple[Any, ...]) -> tuple[tuple[int, float, str], ...]:
    return tuple(_cell_sort_key(cell) for cell in row)


def cells_equal(left: Any, right: Any) -> bool:
    """Two normalised cells, with the numeric tolerance applied."""
    if left is None or right is None:
        # `is`, not `==`: None must not equal 0, "" or False.
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        # Both must be booleans; True == 1 is not a match we want to report.
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(left - right) <= TOLERANCE
    return left == right


@dataclass(frozen=True, slots=True)
class Comparison:
    """The verdict, plus the reason when it is negative.

    The reason is the point: "results_match: false" tells you nothing, while
    "row 2 of 3 differs: 1490.2 vs 1490.55" is the start of a fix. It is written
    into the JSON report for every failed question.
    """

    match: bool
    reason: str | None = None


def compare_results(
    generated_columns: list[str],
    generated_rows: list[Row],
    reference_columns: list[str],
    reference_rows: list[Row],
) -> Comparison:
    """Compare two result sets as unordered multisets of rows.

    `generated_columns` / `reference_columns` are used only for their *length* —
    see the module docstring on aliases versus arity.
    """
    if len(generated_columns) != len(reference_columns):
        return Comparison(
            False,
            f"column count differs: {len(generated_columns)} "
            f"(named {generated_columns}) vs {len(reference_columns)} expected",
        )

    generated = normalize_rows(list(generated_rows))
    reference = normalize_rows(list(reference_rows))

    if len(generated) != len(reference):
        return Comparison(
            False, f"row count differs: {len(generated)} vs {len(reference)} expected"
        )

    # Sorted, then compared pairwise: sorting gives the multiset semantics (order
    # in, order out, duplicates preserved) while the pairwise pass is what lets
    # the float tolerance apply. Hashing normalised tuples into a Counter would
    # have been shorter and would have made 50.0 and 50.000000001 different rows.
    for index, (left, right) in enumerate(
        zip(
            sorted(generated, key=_row_sort_key),
            sorted(reference, key=_row_sort_key),
            strict=True,
        ),
        start=1,
    ):
        for position, (left_cell, right_cell) in enumerate(zip(left, right, strict=True)):
            if not cells_equal(left_cell, right_cell):
                return Comparison(
                    False,
                    f"row {index} of {len(reference)}, column {position + 1} differs: "
                    f"{left_cell!r} vs {right_cell!r} expected",
                )
    return Comparison(True)
