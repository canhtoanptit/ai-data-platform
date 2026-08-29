"""Reads dbt's build artifacts off disk, with an mtime-keyed cache.

dbt writes three JSON files into `<project>/target/` and they are the whole data
source for the catalog, lineage and runs endpoints — no database involved:

| File               | Written by            | What this app takes from it        |
|--------------------|-----------------------|-----------------------------------|
| `manifest.json`    | any dbt command       | the DAG, docs, columns, tests, SQL|
| `run_results.json` | `dbt build/run/test`  | last run's status + timings       |
| `catalog.json`     | `dbt docs generate`   | real warehouse column types       |

They are *build outputs*: this module only ever opens them for reading.

Two things it deliberately does NOT do. It does not parse the artifacts into
domain objects here — the routers do that, so this file stays "get me the JSON"
and the shape knowledge lives next to the endpoints that shape it. And it does
not hold the parsed JSON forever: `manifest.json` is ~900 KB and re-reading it
per request would be wasteful, but caching it forever would mean a `make
local-build` in another terminal has no effect until the API restarts. So the
cache key is the file's mtime — a rebuild invalidates it on the next request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import get_settings

# The three filenames, so a typo is a NameError rather than a 503.
MANIFEST = "manifest.json"
RUN_RESULTS = "run_results.json"
CATALOG = "catalog.json"

# Printed in the 503 body. Both commands, in order: `local-build` produces
# manifest + run_results, `local-docs` adds catalog.json on top.
REBUILD_HINT = "run `make local-build && make local-docs` from the repo root"


class ArtifactsUnavailable(RuntimeError):
    """A required artifact is missing or unreadable.

    Its own class (rather than FileNotFoundError) so the routers can catch
    exactly "dbt hasn't run yet" and answer 503 — a *temporary* condition the
    user can fix — instead of letting it surface as a 500.
    """

    def __init__(self, filename: str, reason: str) -> None:
        self.filename = filename
        super().__init__(f"{filename} is unavailable ({reason}) — {REBUILD_HINT}")


# (mtime, parsed json) per absolute path.
_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def artifacts_dir() -> Path:
    return get_settings().dbt_artifacts_dir


def _read(filename: str) -> dict[str, Any]:
    path = artifacts_dir() / filename
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # Covers the missing file, a missing directory, and a permissions
        # problem — all of them "you can't have this artifact right now".
        raise ArtifactsUnavailable(filename, f"not found at {path}") from None

    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        with path.open(encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        # A half-written file is a real possibility: dbt may be mid-run while a
        # request comes in. Treat it as unavailable, not as a server bug.
        raise ArtifactsUnavailable(filename, type(exc).__name__) from exc

    _cache[path] = (mtime, parsed)
    return parsed


def load_manifest() -> dict[str, Any]:
    """The DAG and every node's docs. Required — no manifest, no catalog page."""
    return _read(MANIFEST)


def load_run_results() -> dict[str, Any]:
    """The last `dbt build`'s per-node status and timing."""
    return _read(RUN_RESULTS)


def load_catalog() -> dict[str, Any] | None:
    """Warehouse column types, or None when `dbt docs generate` hasn't run.

    Optional, unlike the other two: catalog.json only adds `data_type` to
    columns dbt already lists in the manifest. Failing the whole catalog page
    for a missing nicety would be the wrong trade, so callers get None and
    render the types as null.
    """
    try:
        return _read(CATALOG)
    except ArtifactsUnavailable:
        return None
