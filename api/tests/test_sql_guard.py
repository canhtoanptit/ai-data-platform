"""Unit tests for the SQL guard — the security boundary of /api/chat.

Unlike the rest of this suite these are real unit tests with no skipif: the guard
is pure functions over strings, so there is no warehouse, no artifacts and no API
key to depend on, and no reason for them ever to be skipped.

The tests are grouped the way the guard is: what must be allowed (rejecting
valid analytical SQL makes the feature useless), what must be rejected (the
actual boundary), and the LIMIT arithmetic.
"""

import pytest

from app.sql_guard import MAX_ROWS, UnsafeSql, strip_code_fences, validate

MARTS = "analytics_marts"


# --- allowed: this is all legitimate read-only analytics -----------------------


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("select 1", id="trivial"),
        pytest.param(f"select * from {MARTS}.fct_collection_cases", id="select-star"),
        pytest.param(
            f"select case_status, count(*) as n from {MARTS}.fct_collection_cases "
            "group by case_status having count(*) > 1 order by n desc",
            id="aggregate-group-having-order",
        ),
        pytest.param(
            f"select a.team, avg(f.days_past_due) as avg_dpd "
            f"from {MARTS}.fct_collection_cases f "
            f"join {MARTS}.dim_agents a on a.agent_id = f.agent_id "
            "group by a.team",
            id="join",
        ),
        pytest.param(
            f"with per_team as (select team, sum(is_cured) as cured "
            f"from {MARTS}.fct_collection_cases f "
            f"join {MARTS}.dim_agents a using (agent_id) group by team) "
            "select * from per_team order by cured desc",
            id="with-cte",
        ),
        pytest.param(
            f"select customer_id from {MARTS}.dim_customers where customer_id in "
            f"(select customer_id from {MARTS}.fct_collection_cases where is_cured = 1)",
            id="subquery",
        ),
        pytest.param(
            f"select team from {MARTS}.dim_agents union all select 'none'",
            id="union-all",
        ),
        pytest.param(
            f"select count(*) filter (where case_status = 'open') as open_cases "
            f"from {MARTS}.fct_collection_cases",
            id="postgres-filter-clause",
        ),
        pytest.param(
            f"select delinquent_amount::numeric(10,2) from {MARTS}.fct_collection_cases",
            id="postgres-cast-operator",
        ),
        pytest.param(f"select * from {MARTS}.dim_agents;", id="trailing-semicolon"),
    ],
)
def test_allows_read_only_analytics(sql: str) -> None:
    # The assertion is "did not raise" plus "still a select": validate()
    # regenerates the SQL from the parse tree, so the text is normalised.
    assert validate(sql).lower().lstrip("(").startswith(("select", "with"))


# --- rejected: the boundary ---------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected_fragment"),
    [
        pytest.param(
            f"update {MARTS}.dim_agents set team = 'x'", "UPDATE", id="update"
        ),
        pytest.param(f"delete from {MARTS}.dim_agents", "DELETE", id="delete"),
        pytest.param(
            f"insert into {MARTS}.dim_agents values (1)", "INSERT", id="insert"
        ),
        pytest.param(f"drop table {MARTS}.dim_agents", "DROP", id="drop"),
        pytest.param("create table sneaky as select 1", "CREATE", id="create"),
        pytest.param("alter table x add column y int", "ALTER", id="alter"),
        pytest.param(f"truncate table {MARTS}.dim_agents", "TRUNCATE", id="truncate"),
        pytest.param("grant select on x to public", "GRANT", id="grant"),
        pytest.param(
            "copy x from '/etc/passwd'", "only SELECT", id="copy-reads-a-file"
        ),
        pytest.param("vacuum full", "only SELECT", id="vacuum-as-command"),
        pytest.param("set statement_timeout = 0", "only SELECT", id="set-defeats-layer-3"),
        pytest.param("commit", "only SELECT", id="commit-defeats-layer-2"),
    ],
)
def test_rejects_writes_and_ddl(sql: str, expected_fragment: str) -> None:
    with pytest.raises(UnsafeSql, match=expected_fragment):
        validate(sql)


def test_rejects_multiple_statements() -> None:
    """The classic: a legal SELECT with a second statement stapled on.

    Rejected outright rather than truncated to the first statement — running
    half of an unexpected payload is never the safe reading.
    """
    with pytest.raises(UnsafeSql, match="exactly one statement"):
        validate("select 1; drop table analytics_marts.dim_agents")


def test_rejects_a_write_hidden_in_a_cte() -> None:
    """Postgres really does allow a data-modifying CTE, so the walk matters.

    A check that only looked at the top-level node would see a SELECT here.
    """
    with pytest.raises(UnsafeSql, match="DELETE"):
        validate("with gone as (delete from analytics_marts.dim_agents returning *) "
                 "select * from gone")


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("select * from information_schema.tables", id="information_schema"),
        pytest.param("select * from information_schema.columns", id="information_schema-2"),
        pytest.param("select * from pg_catalog.pg_tables", id="pg_catalog-qualified"),
        pytest.param('select * from "pg_catalog"."pg_user"', id="pg_catalog-quoted"),
        pytest.param("select * from PG_CATALOG.PG_TABLES", id="pg_catalog-upper"),
        pytest.param("select * from pg_stat_activity", id="pg_-unqualified"),
        pytest.param("select * from pg_shadow", id="pg_shadow-password-hashes"),
    ],
)
def test_rejects_system_catalogs(sql: str) -> None:
    with pytest.raises(UnsafeSql, match="not allowed"):
        validate(sql)


def test_rejects_pg_functions() -> None:
    """pg_sleep() and pg_read_file() need no table at all."""
    with pytest.raises(UnsafeSql, match="pg_sleep"):
        validate("select pg_sleep(30)")


@pytest.mark.parametrize(
    "sql", [pytest.param("", id="empty"), pytest.param("   \n ", id="whitespace")]
)
def test_rejects_empty_input(sql: str) -> None:
    with pytest.raises(UnsafeSql, match="no SQL"):
        validate(sql)


def test_rejects_unparseable_text() -> None:
    """When the model answers with prose instead of SQL, it must not reach the db."""
    with pytest.raises(UnsafeSql, match="could not parse"):
        validate("I'm sorry, I can't answer that ((")


# --- the LIMIT ----------------------------------------------------------------


def test_adds_a_limit_when_there_is_none() -> None:
    assert f"LIMIT {MAX_ROWS}" in validate("select * from analytics_marts.dim_agents")


def test_clamps_a_limit_that_is_too_large() -> None:
    limited = validate("select * from analytics_marts.dim_customers limit 100000")
    assert f"LIMIT {MAX_ROWS}" in limited
    assert "100000" not in limited


def test_keeps_a_limit_that_is_already_small_enough() -> None:
    limited = validate("select * from analytics_marts.dim_agents limit 5")
    assert "LIMIT 5" in limited
    assert f"LIMIT {MAX_ROWS}" not in limited


def test_limits_a_set_operation_as_a_whole() -> None:
    """A UNION's own LIMIT, not one bolted onto the last branch."""
    limited = validate("select team from analytics_marts.dim_agents union select 'x'")
    assert limited.rstrip().endswith(f"LIMIT {MAX_ROWS}")


def test_an_inner_limit_does_not_count_as_the_outer_one() -> None:
    """`limit 3` inside a subquery bounds the subquery, not the result."""
    limited = validate(
        "select * from (select * from analytics_marts.dim_agents limit 3) t"
    )
    assert limited.rstrip().endswith(f"LIMIT {MAX_ROWS}")


def test_max_rows_is_overridable_for_callers_that_need_less() -> None:
    assert "LIMIT 10" in validate("select * from analytics_marts.dim_agents", max_rows=10)


# --- reading the model's response --------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("select 1", "select 1", id="bare-sql-untouched"),
        pytest.param("```sql\nselect 1\n```", "select 1", id="tagged-fence"),
        pytest.param("```\nselect 1\n```", "select 1", id="untagged-fence"),
        pytest.param(
            "```sql\nselect 1\n```\nHope that helps!",
            "select 1",
            id="prose-after-the-fence",
        ),
        pytest.param("  select 1  ", "select 1", id="surrounding-whitespace"),
    ],
)
def test_strip_code_fences(raw: str, expected: str) -> None:
    assert strip_code_fences(raw) == expected
