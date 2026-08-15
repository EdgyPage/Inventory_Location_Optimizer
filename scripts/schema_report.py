"""schema_report — the CLI over `Schema/compat.py`, and the one place that knows every family.

    python scripts/schema_report.py --report                     # the surface, per family
    python scripts/schema_report.py --capture sim_db <file.db>   # commit a historical shape

WHY THIS IS NOT IN `Schema/`
----------------------------
`Schema/` is the stdlib-only leaf every layer may import, and `context/architecture.yml` forbids
it from importing `opt_persistence`, `optimization`, `generation`, `evaluations`, `visualization`
and `warehouse_core`. But a family exists only once its WRITER's module has been imported, so
anything that reports on *all* families must import all of them — which is exactly what the
`schema` layer may not do.

That import list lived in `Schema/compat.py` briefly, behind `importlib.import_module` on string
literals. It worked, and it was wrong twice over:

  * `context/arch/extract.py` only visits `ast.Import` / `ast.ImportFrom`, so a string-literal
    dynamic import records NO edge. `verify_architecture.py` reported OK on a real violation —
    the gate was green because it could not see, not because the import was legal. (A plain
    `from Optimization.persistence import Picking_Data` in the same function WOULD have produced
    the edge and failed the boundary.)
  * it made the leaf heavy in fact if not in form: the call pulls ~1350 modules, matplotlib,
    pandas, numpy and scipy included, into the package whose own README advertises "stdlib-only,
    imports nothing else in the repo, so every layer may use it". `identity.check_tables` exists
    precisely to stop `Warehouse/catalog/Affinity_Store.py` dragging a data-gen CLI into every
    simulation worker; this would have re-opened that door one level down.

There is also a genuine cycle: `Optimization/persistence/Picking_Data.py` imports `Schema.compat`
at module scope, and the family list imports `Picking_Data` back. Deferring it inside a function
survives, but only by accident of ordering. Here, in the `scripts` layer, importing downward is
simply legal and the cycle does not exist.

`Schema/compat.py` stays a pure library: it takes a family NAME and reads the committed shape
store, and never learns who writes what.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # entry-script bootstrap

from Schema import compat, connect, identity
from Schema.shape import canonical_shape, describe_diff, diff_shapes, observed_id

#: Every module that registers a `Schema.identity.Family`, newest concern last.
#:
#: Listed rather than discovered by walking the tree: importing a module has side effects here
#: (matplotlib, pandas and scipy all arrive with the generation CLIs), so which modules get
#: imported is a decision, not a search. `Tests/architecture/test_schema_compatibility.py` asserts
#: this list covers every family the identity gate expects, so a new family that forgets to land
#: here fails CI rather than silently vanishing from `--report`.
FAMILY_MODULES = (
    'Optimization.persistence.Picking_Data',        # sim_db, keyframes_db
    'Optimization.persistence.Warehouse_Data',      # warehouse_db
    'Optimization.persistence.runtime_metrics',     # runtime_metrics_db
    'Visualization.cache_schema',                   # viz_cache_db
    'Warehouse.generation.generate_inventory',      # inventory_db
    'Warehouse.generation.generate_affinity',       # affinity_db
)


def import_families() -> None:
    """Populate `Schema.identity`'s registry by importing every writer."""
    for mod in FAMILY_MODULES:
        importlib.import_module(mod)


# ── report ───────────────────────────────────────────────────────────────────────

def report(out=print) -> int:
    """Print each family's guaranteed/conditional surface. Exit 1 if any id is unrecoverable."""
    import_families()
    rc = 0
    for name in identity.families():
        fam = identity.get(name)
        missing = compat.unrecoverable_ids(name)
        out(f'\n{name}  ({len(fam.supported_ids())} vetted id(s))')
        for sid in fam.supported_ids():
            tag = ('declared' if sid == fam.declared_id()
                   else ('committed' if compat.load_shape(name, sid) else 'UNRECOVERABLE'))
            out(f'    {sid}  {tag}')
        if missing:
            rc = 1
            out(f'    -> surface unknowable until {", ".join(missing)} is captured or dropped')
            continue
        g = compat.guaranteed_surface(name)
        c = compat.conditional_surface(name)
        out(f'    guaranteed : {", ".join(sorted(g)) or "(none)"}')
        out(f'    conditional: {", ".join(f"{t}({len(v)})" for t, v in c.items()) or "(none)"}')
    return rc


# ── capture ──────────────────────────────────────────────────────────────────────

def capture(family: str, path: str, note: str = '', out=print) -> int:
    """Commit the shape of a real file of a vetted historical vintage."""
    import_families()
    fam = identity.get(family)
    con = connect.read_only(path)
    try:
        sid, shape = observed_id(con), canonical_shape(con)
    finally:
        con.close()
    if sid not in fam.supported_ids():
        out(f'refusing: {os.path.basename(path)} is {sid}, which {family} does not vet.\n'
            f'  {describe_diff(diff_shapes(fam.declared_shape(), shape))}')
        return 1
    if sid == fam.declared_id():
        out(f'nothing to do: {sid} is the DECLARED shape, always re-derivable from the writer.')
        return 0
    # A run NAME, never a path: a machine-local path in a tracked file is blocked by
    # `context/guards/path_guard.py`, and would be useless to anyone else besides.
    run = next((p for p in os.path.abspath(path).replace('\\', '/').split('/')
                if p.startswith('comparison_')), 'unknown run')
    dest = compat.write_shape(family, sid, shape, captured_from=run, note=note)
    out(f'captured {sid} -> {os.path.relpath(dest, os.path.dirname(compat.SHAPES_DIR))}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Report or capture DB-shape compatibility surfaces.')
    ap.add_argument('--report', action='store_true',
                    help="print each family's guaranteed/conditional surface; exit 1 if any "
                         'vetted id has no committed shape')
    ap.add_argument('--capture', nargs=2, metavar=('FAMILY', 'DB'),
                    help='commit the shape of a real file of a vetted historical vintage')
    ap.add_argument('--note', default='', help='why this vintage exists, for the document')
    args = ap.parse_args(argv)
    if args.capture:
        return capture(args.capture[0], args.capture[1], args.note)
    return report()


if __name__ == '__main__':                                  # pragma: no cover
    raise SystemExit(main())
