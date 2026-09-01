"""Eval harness for the NL->SQL feature.

Deliberately a sibling of `app/`, not a module inside it: this is a *test
instrument*, not part of the served API, and it is excluded from the Docker image
(see the wheel's `packages = ["app"]`) so the runtime carries no YAML parsing and
no golden data. It imports `app.nl2sql` so that what it measures is exactly what
the endpoint runs.

  golden.yaml   the questions + the SQL a human would have written (edit this)
  compare.py    result-set comparison — the scoring rules, and the unit tests' target
  run.py        the runner: `uv run python -m evals.run`, or `make eval`
  results/      JSON reports, one per run (gitignored)
"""
