"""Data catalog and lineage, parsed out of dbt's manifest.

No SQL here — unlike the other routers, the data source is
`anz_banking/target/*.json`, not the warehouse. dbt already knows every model's
docs, columns, tests and upstream/downstream edges; this exposes that as JSON so
the dashboard can render a catalog and a DAG without shelling out to `dbt docs`.

A missing artifact raises `ArtifactsUnavailable`, which main.py's exception
handler turns into a 503 — so nothing here needs a try/except.
"""

from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, status

from ..dbt_artifacts import load_catalog, load_manifest
from ..schemas_catalog import (
    Lineage,
    LineageEdge,
    LineageNode,
    ModelDetail,
    ModelSummary,
    NodeColumn,
)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# What the catalog lists and what the DAG draws. Tests are excluded from both:
# there are 39 of them and they are properties *of* a node, shown on the node.
CATALOG_TYPES = frozenset({"model", "seed", "snapshot"})

# Model layers, as folder names under `models/`. dbt puts the folder in the
# node's `path`, so the layer is read off that rather than declared anywhere.
MODEL_LAYERS = frozenset({"staging", "intermediate", "marts"})

# Resource types that get their layer from the type itself: a seed has no
# folder under models/, and a source has no file at all beyond its .yml.
LAYER_BY_TYPE = {"seed": "seed", "snapshot": "snapshot", "source": "source"}


def _layer(node: dict[str, Any]) -> str:
    """staging | intermediate | marts | seed | snapshot | source | unknown."""
    by_type = LAYER_BY_TYPE.get(node["resource_type"])
    if by_type is not None:
        return by_type
    # `path` is relative to models/, e.g. "staging/stg_collections__cases.sql".
    # A model straight in models/ has no folder part, hence the length check.
    parts = PurePosixPath(node.get("path") or "").parts
    folder = parts[0] if len(parts) > 1 else ""
    return folder if folder in MODEL_LAYERS else "unknown"


def _parents(node: dict[str, Any]) -> list[str]:
    """The node's upstream unique_ids.

    `.get("nodes", [])` rather than indexing: a seed's depends_on is
    `{"macros": []}` with no "nodes" key at all, because a CSV depends on
    nothing.
    """
    return node.get("depends_on", {}).get("nodes", [])


def _test_label(test: dict[str, Any]) -> str:
    """"unique" for a generic test, the file name for a singular one.

    Generic tests get a mangled unique name
    (`unique_fct_collection_cases_case_id`); `test_metadata.name` is the part a
    human wants. Singular tests (a .sql file in tests/) have no test_metadata,
    and there their own name is already the readable thing.
    """
    metadata = test.get("test_metadata") or {}
    return metadata.get("name") or test["name"]


def _tests_by_owner(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group test nodes by the node they test.

    `attached_node` is the right attribution when dbt provides it. A
    `relationships` test depends on *two* models — the child it is declared on
    and the parent it points at — so attributing by `depends_on` would count it
    twice and inflate the parent's test count. Singular tests have no
    attached_node, and for those depends_on is all there is.
    """
    owners: dict[str, list[dict[str, Any]]] = {}
    for test in manifest["nodes"].values():
        if test["resource_type"] != "test":
            continue
        attached = test.get("attached_node")
        targets = [attached] if attached else _parents(test)
        for target in targets:
            owners.setdefault(target, []).append(test)
    return owners


def _catalog_nodes(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        unique_id: node
        for unique_id, node in manifest["nodes"].items()
        if node["resource_type"] in CATALOG_TYPES
    }


def _columns(
    node: dict[str, Any],
    catalog_node: dict[str, Any] | None,
    tests: list[dict[str, Any]],
) -> list[NodeColumn]:
    """Union of the documented columns and the ones actually in the warehouse.

    manifest.json lists only columns someone wrote a `.yml` entry for (3 of
    fct_collection_cases' 18); catalog.json lists all 18 with their types but no
    descriptions. Neither alone is the column list a catalog should show.

    Matching is case-insensitive because the two halves disagree on case
    depending on the adapter: Postgres reports lower-case column names in the
    catalog, Snowflake reports upper-case, and the .yml is lower-case in both.
    The warehouse's spelling wins for display, since that is what you would
    type in a query.
    """
    documented = {name.lower(): column for name, column in (node.get("columns") or {}).items()}
    warehouse = (catalog_node or {}).get("columns") or {}

    tests_by_column: dict[str, list[str]] = {}
    for test in tests:
        column_name = test.get("column_name")
        if column_name:
            tests_by_column.setdefault(column_name.lower(), []).append(_test_label(test))

    columns: list[NodeColumn] = []
    seen: set[str] = set()
    # Warehouse order (catalog's `index`) rather than alphabetical: it is the
    # order `select *` returns, which is how people picture the table.
    for entry in sorted(warehouse.values(), key=lambda column: column.get("index", 0)):
        name = entry["name"]
        key = name.lower()
        seen.add(key)
        columns.append(
            NodeColumn(
                name=name,
                data_type=entry.get("type"),
                description=(documented.get(key, {}).get("description") or None),
                tests=tests_by_column.get(key, []),
            )
        )
    # Documented but not in the warehouse: an ephemeral model (never built, so
    # never in the catalog), or a .yml entry for a column that was renamed.
    for key, column in documented.items():
        if key in seen:
            continue
        columns.append(
            NodeColumn(
                name=column["name"],
                data_type=column.get("data_type"),
                description=column.get("description") or None,
                tests=tests_by_column.get(key, []),
            )
        )
    return columns


def _summary(
    node: dict[str, Any],
    catalog_node: dict[str, Any] | None,
    tests: list[dict[str, Any]],
) -> ModelSummary:
    warehouse_columns = (catalog_node or {}).get("columns") or {}
    documented = {name.lower() for name in (node.get("columns") or {})}
    return ModelSummary(
        unique_id=node["unique_id"],
        name=node["name"],
        resource_type=node["resource_type"],
        layer=_layer(node),
        schema_name=node["schema"],
        materialization=node["config"]["materialized"],
        description=node.get("description") or "",
        # Same union as _columns(), counted without building the objects.
        column_count=len({name.lower() for name in warehouse_columns} | documented),
        test_count=len(tests),
    )


@router.get("/models", summary="Every model, seed and snapshot dbt knows about")
def list_models() -> list[ModelSummary]:
    manifest = load_manifest()
    catalog = load_catalog() or {}
    catalog_nodes = catalog.get("nodes", {})
    owners = _tests_by_owner(manifest)

    summaries = [
        _summary(node, catalog_nodes.get(unique_id), owners.get(unique_id, []))
        for unique_id, node in _catalog_nodes(manifest).items()
    ]
    # Layer order = pipeline order, so the list reads the way the data flows.
    # dbt returns nodes in parse order, which is neither stable nor meaningful.
    layer_rank = ["seed", "staging", "intermediate", "marts", "snapshot", "unknown"]
    return sorted(
        summaries,
        key=lambda summary: (
            layer_rank.index(summary.layer) if summary.layer in layer_rank else len(layer_rank),
            summary.name,
        ),
    )


@router.get(
    "/models/{name}",
    summary="One model with its columns, tests, neighbours and SQL",
    responses={404: {"description": "No model, seed or snapshot with that name"}},
)
def get_model(name: str) -> ModelDetail:
    manifest = load_manifest()
    nodes = _catalog_nodes(manifest)
    # Keyed by name, not unique_id: the URL is /models/fct_collection_cases, not
    # /models/model.anz_banking.fct_collection_cases.
    by_name = {node["name"]: node for node in nodes.values()}
    node = by_name.get(name)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"model {name!r} not found in the dbt manifest",
        )

    unique_id = node["unique_id"]
    catalog = load_catalog() or {}
    tests = _tests_by_owner(manifest).get(unique_id, [])

    # Names of every node in the manifest, sources included, for the neighbour
    # lists. Sources are here (a staging model's parent is a source) even
    # though they are not in the catalog listing.
    names = {node_id: value["name"] for node_id, value in manifest["nodes"].items()}
    names.update(
        {node_id: value["name"] for node_id, value in manifest["sources"].items()}
    )

    def neighbour_names(node_ids: list[str]) -> list[str]:
        # Deduped, because in this project every raw table is both a seed and a
        # declared source pointing at the same relation (see the comment at the
        # top of any stg_ model), so a staging model's parents are the same name
        # twice. The lineage graph keeps both nodes; a link list should not.
        seen: dict[str, None] = {}
        for node_id in node_ids:
            label = names.get(node_id)
            if label is not None:
                seen[label] = None
        return list(seen)

    children = [
        child
        for child in manifest["child_map"].get(unique_id, [])
        # Tests are children in the DAG; they are shown as tests, not as
        # downstream models.
        if child in nodes
    ]

    summary = _summary(node, catalog.get("nodes", {}).get(unique_id), tests)
    return ModelDetail(
        **summary.model_dump(),
        columns=_columns(node, catalog.get("nodes", {}).get(unique_id), tests),
        table_tests=[_test_label(test) for test in tests if not test.get("column_name")],
        depends_on=neighbour_names(_parents(node)),
        referenced_by=neighbour_names(children),
        # Seeds are CSVs: raw_code is an empty string, and null reads better in
        # the client than a blank code block.
        raw_sql=node.get("raw_code") or None,
        compiled_sql=node.get("compiled_code") or None,
    )


@router.get("/lineage", summary="The whole DAG: sources, seeds, models, snapshots")
def get_lineage() -> Lineage:
    manifest = load_manifest()
    nodes: dict[str, dict[str, Any]] = {
        **_catalog_nodes(manifest),
        # Sources have no `nodes` entry — they live in their own top-level key —
        # but they are where the DAG starts, so the graph is wrong without them.
        **manifest["sources"],
    }

    lineage_nodes = [
        LineageNode(
            id=unique_id,
            name=node["name"],
            resource_type=node["resource_type"],
            layer=_layer(node),
        )
        for unique_id, node in nodes.items()
    ]

    # parent_map is keyed by child, so each entry is a batch of incoming edges.
    # Both endpoints are filtered against `nodes`, which drops the test nodes
    # (they are parent_map keys too) in one place.
    edges = [
        LineageEdge(source=parent, target=child)
        for child, parents in manifest["parent_map"].items()
        if child in nodes
        for parent in parents
        if parent in nodes
    ]
    return Lineage(nodes=lineage_nodes, edges=edges)
