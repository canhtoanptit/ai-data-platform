"""Response models for the dbt-artifact endpoints (catalog, lineage, runs).

Separate from schemas.py on purpose: that file models *mart rows* (warehouse
data), this one models *metadata about the pipeline that built them*. Two
different sources of truth, two files.

One naming wrinkle runs through the file. `schema` cannot be a field name —
pydantic warns that it shadows `BaseModel.schema` — so the field is
`schema_name` in Python and serialised as `schema` via `serialization_alias`.
FastAPI serialises responses with `by_alias=True`, so clients see `schema`.
"""

from pydantic import BaseModel, ConfigDict, Field

# Every model/seed/snapshot/source is filed under exactly one of these. Derived
# from the node's folder (models/staging/... -> "staging") or, for the resource
# types that have no folder of their own, from the resource type itself.
# "unknown" is the escape hatch for a model in some other directory: it shows up
# labelled rather than vanishing from the list.
Layer = str


class NodeColumn(BaseModel):
    """One column of a model, merged from two artifacts.

    dbt's manifest only lists the columns someone documented in a `.yml`;
    catalog.json lists what is actually in the warehouse, with its type. The
    catalog endpoint unions them, which is why both halves are optional: a
    documented-but-not-yet-built column has no `data_type`, and a built column
    nobody documented has no `description`.
    """

    name: str
    data_type: str | None = Field(
        default=None,
        description="Warehouse type from catalog.json; null if `dbt docs generate` hasn't run",
        examples=["integer"],
    )
    description: str | None = None
    tests: list[str] = Field(
        default_factory=list,
        description="Names of the dbt tests on this column",
        examples=[["not_null", "unique"]],
    )


class ModelSummary(BaseModel):
    """A row in the catalog listing: one model, seed or snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    unique_id: str = Field(examples=["model.anz_banking.fct_collection_cases"])
    name: str = Field(examples=["fct_collection_cases"])
    resource_type: str = Field(examples=["model"])
    layer: Layer = Field(examples=["marts"])
    schema_name: str = Field(serialization_alias="schema", examples=["analytics_marts"])
    materialization: str = Field(examples=["table"])
    description: str = Field(description="Empty string when the model is undocumented")
    column_count: int
    test_count: int


class ModelDetail(ModelSummary):
    """The listing row plus everything the detail panel shows."""

    columns: list[NodeColumn] = Field(default_factory=list)
    table_tests: list[str] = Field(
        default_factory=list,
        # Column tests hang off their column; these are the ones that don't have
        # one (a singular test, or dbt_utils' unique_combination_of_columns).
        # Kept separate so `test_count` == len(table_tests) + all column tests,
        # i.e. nothing in the count is invisible on the page.
        description="Tests on the model as a whole rather than on one column",
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Names of the nodes this one reads from"
    )
    referenced_by: list[str] = Field(
        default_factory=list, description="Names of the nodes that read from this one"
    )
    raw_sql: str | None = Field(
        default=None, description="The model file as written, Jinja included"
    )
    compiled_sql: str | None = Field(
        default=None, description="After Jinja; null for seeds and un-run models"
    )


class LineageNode(BaseModel):
    id: str = Field(description="dbt unique_id — the edge endpoints reference this")
    name: str
    resource_type: str
    layer: Layer


class LineageEdge(BaseModel):
    source: str = Field(description="unique_id of the upstream node")
    target: str = Field(description="unique_id of the downstream node")


class Lineage(BaseModel):
    nodes: list[LineageNode]
    edges: list[LineageEdge]


class RunCounts(BaseModel):
    """Tally of the last run.

    dbt statuses split by node kind: models, seeds and snapshots report
    success/error/skipped, tests report pass/fail/warn (and can also be
    skipped). Keeping all six in one flat object means the UI can show
    "models built" and "tests passed" without knowing dbt's status enums.
    """

    success: int = 0
    error: int = 0
    skipped: int = 0
    # `pass` is a Python keyword, so the field is pass_ with an alias.
    pass_: int = Field(default=0, serialization_alias="pass")
    fail: int = 0
    warn: int = 0


class RunResultRow(BaseModel):
    unique_id: str
    name: str
    resource_type: str
    status: str = Field(examples=["success", "pass", "fail"])
    execution_time: float = Field(description="Seconds")
    message: str | None = Field(
        default=None, description="dbt's message; the failure reason when it failed"
    )


class LatestRun(BaseModel):
    generated_at: str = Field(
        description="When dbt wrote run_results.json (ISO 8601, UTC)",
        examples=["2026-08-28T12:55:42.553494Z"],
    )
    elapsed_total: float = Field(description="Wall-clock seconds for the whole run")
    counts: RunCounts
    results: list[RunResultRow]
