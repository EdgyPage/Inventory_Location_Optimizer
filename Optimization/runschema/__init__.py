"""runschema — the VERSIONED contract for a simulation run's on-disk tree.

Downstream tools (analysis, graphs, docs ingest, the replay viewer) resolve paths through a
resolver from this package instead of joining strings.  A structural change to the tree therefore
means adding a ``vN.py`` and regenerating ``Optimization/schemas/run_tree.vN.json`` — not editing
path arithmetic scattered across the analysis branch.

    from Optimization import runschema
    rt = runschema.resolver_for(base_dir)        # version chosen by the run's OWN descriptor
    for cell, cr, sim_db in rt.sim_dbs():
        ...

Version selection reads ``schema_version`` out of the run's ``run_layout.json``, so an OLD run keeps
analyzing with the resolver it was written by even after the code moves on.  ``RUN_TREE_VERSION`` is
what a NEW run stamps.

There is deliberately NO pre-v1 fallback: v1 is the tree as of the cell-matrix refactor, and runs
older than that are not supported (they predate ``run_layout.json`` entirely).  ``preflight.py``
keeps the committed contract honest by proving it against real canary runs before a simulation.
"""
from __future__ import annotations

import os

from Optimization.runschema.v1 import RunTreeV1

# The version a NEW run stamps into its run_layout.json.  Bumped by runschema.preflight when a
# canary proves the tree shape changed; never edit by hand without regenerating the contract.
RUN_TREE_VERSION = 1

# schema_version -> resolver class.  Add the new class here when you add a vN.py.
_RESOLVERS = {
    1: RunTreeV1,
}

LATEST = _RESOLVERS[RUN_TREE_VERSION]


class UnsupportedRunTree(Exception):
    """Raised for a run whose tree version this build cannot resolve."""


def resolver_for(base_dir: str):
    """Return the resolver matching the run's own ``run_layout.json`` ``schema_version``.

    Raises UnsupportedRunTree when the descriptor is missing (a pre-v1 run) or names a version this
    build doesn't know — a loud failure is the point.  Silently guessing the layout is what made
    every downstream tool drift in the first place.
    """
    from Optimization.sim_manifest import read_run_layout
    base = os.path.abspath(base_dir)
    if not os.path.isdir(base):
        raise UnsupportedRunTree(f'not a directory: {base}')
    layout = read_run_layout(base)
    if layout is None:
        raise UnsupportedRunTree(
            f'no run_layout.json in {base} — pre-v1 run, unsupported. Runs predating the '
            f'cell-matrix refactor have no descriptor and cannot be resolved.')
    ver = layout.get('schema_version')
    if ver is None:
        raise UnsupportedRunTree(
            f'run_layout.json in {base} has no `schema_version` — pre-v1 descriptor, unsupported.')
    cls = _RESOLVERS.get(int(ver))
    if cls is None:
        raise UnsupportedRunTree(
            f'run_layout.json in {base} declares schema_version={ver}, but this build only knows '
            f'{sorted(_RESOLVERS)}. Check out the code that produced the run, or re-run it.')
    return cls(base, layout=layout)


def resolver_for_new_run(base_dir: str, layout: dict | None = None):
    """Resolver for a run being CREATED right now (its descriptor may not be written yet).

    Always the LATEST version — a new run is stamped with RUN_TREE_VERSION by definition.
    """
    return LATEST(base_dir, layout=layout)


def resolve_base_dir(name: str) -> str:
    """Resolve a CLI run argument: an absolute path as-is, a bare name against COMPARISON_OUTPUT_DIR.

    Every downstream CLI over a run tree shares this, so `python -m Optimization.run_whatif_delta
    comparison_20260728_120000` behaves the same as the analysis hub instead of only resolving
    relative to the current directory.
    """
    from Optimization.sim_config import _OUTPUT_DIR
    return os.path.abspath(name if os.path.isabs(name) else os.path.join(_OUTPUT_DIR, name))
