"""The schema briefing the LLM writes SQL against.

Text-to-SQL is only as good as the schema description it is given, and the best
available description of these tables is the one dbt already holds: the model
descriptions from `_marts.yml`, the real warehouse column types from
`catalog.json`, and any documented column descriptions. So the briefing is
*generated from the pipeline*, exactly like the catalog page — add a column to a
mart, rebuild, and the LLM knows about it on the next request. Nothing to
maintain by hand, and nothing that can silently disagree with the warehouse.

Two deliberate restrictions:

- **Marts only.** Staging and intermediate models are implementation detail; the
  marts are the layer with tested grain and documented meaning. Narrowing the
  briefing also narrows the model's options, which measurably improves the SQL
  — a smaller, curated schema is the cheapest text-to-SQL accuracy win there is.
- **No row data.** The briefing is structure and vocabulary, never contents. The
  only rows that ever leave the warehouse are the ones the user's own query
  returned.

The DOMAIN_NOTES at the bottom are the part dbt cannot supply: the handful of
value conventions that a model gets wrong on its first try (that `is_cured` is a
0/1 int rather than a boolean, that `delinquency_bucket` is ordinal text, that
"cure rate" has a specific definition here). They are short on purpose — this is
a prompt, not documentation.
"""

from __future__ import annotations

from .dbt_artifacts import artifacts_fingerprint, load_catalog, load_manifest
from .routers.catalog import merge_columns

# The gold layer, and the only tables the chat endpoint will describe or query.
# Listed explicitly rather than filtered by folder: this is an allow-list that
# the SQL prompt and the reader of this file can both check at a glance, and a
# new model appearing under models/marts/ should be a decision, not a surprise.
MARTS_MODELS = (
    "fct_collection_cases",
    "dim_customers",
    "dim_agents",
    "collections_performance",
)

# Value conventions the column types do not convey. Kept to four lines: prompt
# tokens spent here compete with the schema itself.
DOMAIN_NOTES = (
    "case_status is one of 'open', 'resolved', 'written_off'.",
    "delinquency_bucket is ordinal text, in order: "
    "'current', '1-30 dpd', '31-60 dpd', '61-90 dpd', '90+ dpd'. "
    "It does not sort alphabetically — order by a CASE expression if order matters.",
    "is_cured and is_written_off are 0/1 integers, not booleans, so they can be "
    "summed: cure rate = sum(is_cured) * 100.0 / nullif(count(*), 0).",
    "collections_performance is fct_collection_cases already aggregated to "
    "(team, delinquency_bucket) — prefer it for team-level KPI questions, and "
    "note that `team` comes from the agent and is null for unassigned cases.",
)

# (fingerprint, briefing). One entry, because there is one briefing; the key is
# the artifact mtimes, so a dbt rebuild invalidates it. Same bargain as
# dbt_artifacts.py: don't re-derive per request, don't cache past a rebuild.
_cache: tuple[tuple[float | None, ...], str] | None = None


def _model_nodes() -> dict[str, dict[str, object]]:
    """The four marts nodes from the manifest, keyed by model name.

    A mart missing from the manifest is skipped rather than raising: the
    briefing is still usable with three tables, and failing the whole endpoint
    because someone renamed a model would be the wrong trade. A *missing
    manifest* is different — load_manifest() raises ArtifactsUnavailable, and
    main.py turns that into the same 503 the catalog pages give.
    """
    manifest = load_manifest()
    wanted = set(MARTS_MODELS)
    return {
        node["name"]: node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model" and node["name"] in wanted
    }


def _table_block(name: str, node: dict, catalog_node: dict | None) -> str:
    """One table: qualified name, description, then one line per column."""
    schema = node["schema"]
    lines = [f"table {schema}.{name}"]

    description = " ".join((node.get("description") or "").split())
    if description:
        lines.append(f"  purpose: {description}")

    # tests=[] — merge_columns() annotates columns with their dbt tests for the
    # catalog page, but "this column has a not_null test" is not information the
    # SQL writer can use.
    for column in merge_columns(node, catalog_node, []):
        parts = [f"  - {column.name}"]
        if column.data_type:
            parts.append(column.data_type)
        if column.description:
            parts.append(f"-- {' '.join(column.description.split())}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def build_schema_context() -> str:
    """The full briefing: four tables with their columns, plus the domain notes.

    Cached against the dbt artifacts' mtimes — see `_cache`.
    """
    global _cache

    fingerprint = artifacts_fingerprint()
    if _cache is not None and _cache[0] == fingerprint:
        return _cache[1]

    nodes = _model_nodes()
    catalog_nodes = (load_catalog() or {}).get("nodes", {})

    blocks = [
        _table_block(name, node, catalog_nodes.get(node["unique_id"]))
        # MARTS_MODELS order, not manifest order: the fact table first, so the
        # most useful table is the one the model reads first.
        for name in MARTS_MODELS
        if (node := nodes.get(name)) is not None
    ]

    notes = "\n".join(f"- {note}" for note in DOMAIN_NOTES)
    context = "\n\n".join(blocks) + "\n\nNotes on the data:\n" + notes

    _cache = (fingerprint, context)
    return context
