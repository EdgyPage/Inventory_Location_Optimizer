"""runschema — the CONTENT-ADDRESSED contract for a simulation run's on-disk tree.

Downstream tools (analysis, graphs, docs ingest, the replay viewer) resolve paths through a resolver
from this package instead of joining strings.  A structural change to the tree means editing
``schema.py``; its identity follows automatically.

    from Optimization import runschema
    rt = runschema.resolver_for(base_dir)        # the schema the run ITSELF was written with
    for cell, cr, sim_db in rt.sim_dbs():
        ...

WHY THERE IS NO VERSION NUMBER
------------------------------
A schema is identified by the sha256 of its own declared shape
(``Optimization/schemas/run_tree/<short>.json``), so:

  * nothing to pick, register, or increment — the id is derived, and re-derivable by anyone;
  * two branches that change the tree differently get different ids instead of both calling
    themselves "v2";
  * a run records the exact schema that produced it, so an OLD run stays resolvable forever;
  * there is no ``LATEST`` to compute — "current" is a named pointer (``INDEX.json``'s `head`),
    because hashes have no natural order.

Compatibility is negotiated by FEATURE, not by comparing numbers: a contract lists the template
vocabulary it uses and ``resolver.SUPPORTED_FEATURES`` says what this build implements, so an
unreadable contract fails by naming the missing feature.

There is deliberately no support for runs predating the cell-matrix refactor — they have no
descriptor at all.  ``preflight.py`` keeps the committed store honest by proving it against real
canary runs before a simulation.
"""
from __future__ import annotations

import os

from Optimization.runschema.resolver import RunTree, SUPPORTED_FEATURES


class UnsupportedRunTree(Exception):
    """Raised for a run whose tree schema this build cannot resolve."""


def head() -> str | None:
    """The schema id a NEW run stamps (INDEX.json's `head`), or None if the store is empty."""
    from Optimization.runschema import contract
    return contract.head()


def _resolver_for_contract(base: str, doc: dict, layout: dict | None) -> RunTree:
    missing = sorted(set(doc.get('features', [])) - set(SUPPORTED_FEATURES))
    if missing:
        raise UnsupportedRunTree(
            f'run at {base} uses run-tree schema {doc["schema_id"]}, which needs feature(s) '
            f'{missing} that this build does not implement. Check out the code that produced the '
            f'run, or add support to Optimization/runschema/resolver.py.')
    return RunTree(base, doc, layout=layout)


def resolver_for(base_dir: str) -> RunTree:
    """Return a resolver bound to the schema the run's own ``run_layout.json`` records.

    Raises UnsupportedRunTree when the descriptor is missing, carries no `schema_id`, or names a
    schema this checkout has no document for — a loud failure is the point.  Silently guessing the
    layout is what made every downstream tool drift in the first place.
    """
    from Optimization.runschema import contract
    from Optimization.runschema.sim_manifest import read_run_layout

    base = os.path.abspath(base_dir)
    if not os.path.isdir(base):
        raise UnsupportedRunTree(f'not a directory: {base}')
    layout = read_run_layout(base)
    if layout is None:
        raise UnsupportedRunTree(
            f'no run_layout.json in {base} — unsupported. Runs predating the cell-matrix refactor '
            f'have no descriptor and cannot be resolved.')
    sid = layout.get('schema_id')
    if not sid:
        raise UnsupportedRunTree(
            f'run_layout.json in {base} has no `schema_id` — pre-content-addressed descriptor, '
            f'unsupported.')
    doc = contract.load(sid)
    if doc is None:
        known = sorted(contract.short_id(k) for k in contract.load_all())
        raise UnsupportedRunTree(
            f'run_layout.json in {base} declares run-tree schema {contract.short_id(sid)}, but no '
            f'document for it is committed here. Known: {known or "(none)"}. Check out the commit '
            f'that produced the run.')
    if doc.get('schema_id') != sid:
        raise UnsupportedRunTree(
            f'stored document for {contract.short_id(sid)} declares a different id '
            f'({doc.get("schema_id")}) — the schema store is corrupt.')
    return _resolver_for_contract(base, doc, layout)


def resolve_base_dir(name: str) -> str:
    """Resolve a CLI run argument: an absolute path as-is, a bare name against COMPARISON_OUTPUT_DIR.

    Every downstream CLI over a run tree shares this, so `python -m Optimization.run_whatif_delta
    comparison_20260728_120000` behaves the same as the analysis hub instead of only resolving
    relative to the current directory.
    """
    from Optimization.config.sim_config import _OUTPUT_DIR
    return os.path.abspath(name if os.path.isabs(name) else os.path.join(_OUTPUT_DIR, name))
