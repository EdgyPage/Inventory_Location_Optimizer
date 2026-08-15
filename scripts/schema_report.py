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


# ── sync / adopt: why adding a known_id stopped being archaeology ────────────────
#
# The first version of this store held only HISTORICAL shapes, and that made every DDL change an
# excavation: once `declared_id()` moved, the outgoing shape existed nowhere except in files on a
# drive, and recovering it meant finding one of that exact vintage (`sim_db/2b7913bcd7e6` had no
# surviving file at all and had to be reconstructed).
#
# `--sync` removes the problem instead of automating the dig: commit the DECLARED shape too, every
# time. Then the moment a DDL edit moves the id, the PREVIOUS shape is already on disk — captured
# while it was still the current one, from the writer's own DDL, with no archive involved. The
# outgoing shape can never be lost again, because it was never only in the past.
#
# `--adopt` is then just bookkeeping: a document whose id no family vets any more IS the outgoing
# shape, and it names itself.

def sync(out=print) -> int:
    """Commit the DECLARED shape of every family that has not been committed yet. Idempotent.

    Run this BEFORE changing any DDL — and it is safe to run always, which is the point. A
    declared shape is re-derivable today and gone tomorrow; committing it is what makes the next
    change adoptable without an archive.
    """
    import_families()
    wrote = []
    for name in identity.families():
        fam = identity.get(name)
        sid = fam.declared_id()
        if compat.load_shape(name, sid) is not None:
            continue
        compat.write_shape(
            name, sid, fam.declared_shape(), captured_from='declared (built from the writer DDL)',
            note='The DECLARED shape at the time it was current. Committed by --sync so that when '
                 'the DDL next moves, this outgoing shape is already on disk and needs no archive '
                 'lookup to recover.')
        wrote.append(f'{name}/{sid}')
    out(f'sync: committed {len(wrote)} declared shape(s)' + (f' - {", ".join(wrote)}' if wrote
                                                             else ' (already current)'))
    return 0


def adopt(out=print) -> int:
    """Report every committed shape no family vets any more - the outgoing shapes to adopt.

    Exit 1 when there is something to adopt, so a hook or CI can catch a DDL change that shipped
    without its `known_ids` entry.
    """
    import_families()
    pending = 0
    for name in identity.families():
        fam = identity.get(name)
        supported = set(fam.supported_ids())
        d = os.path.join(compat.SHAPES_DIR, name)
        on_disk = ({fn[:-len('.json')] for fn in os.listdir(d) if fn.endswith('.json')}
                   if os.path.isdir(d) else set())
        orphans = sorted(on_disk - supported)
        if not orphans:
            continue
        pending += len(orphans)
        out(f'\n{name}: declared id is now {fam.declared_id()}, and {len(orphans)} committed '
            f'shape(s) are no longer vetted:')
        for sid in orphans:
            out(f'    {sid}')
        out('  These ARE the outgoing shapes. Add them to the family\'s `known_ids`, with a '
            'comment naming the window of commits each covers:')
        out(f'    known_ids=({", ".join(repr(s) for s in orphans + sorted(fam.known_ids))},)')
        out('  Entries are ADDED, never replaced - dropping one orphans every file written with '
            'it. If a shape genuinely should stop being readable, delete its document too and say '
            'why in the commit.')
    if not pending:
        out('adopt: nothing to adopt - every committed shape is still vetted.')
    return 1 if pending else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Report or capture DB-shape compatibility surfaces.')
    ap.add_argument('--report', action='store_true',
                    help="print each family's guaranteed/conditional surface; exit 1 if any "
                         'vetted id has no committed shape')
    ap.add_argument('--capture', nargs=2, metavar=('FAMILY', 'DB'),
                    help='commit the shape of a real file of a vetted historical vintage')
    ap.add_argument('--sync', action='store_true',
                    help="commit every family's CURRENT declared shape (idempotent). Run before "
                         'changing a DDL so the outgoing shape survives the change.')
    ap.add_argument('--adopt', action='store_true',
                    help='list committed shapes no family vets any more - the outgoing shapes '
                         'a DDL change left behind. Exits 1 when there is something to adopt.')
    ap.add_argument('--note', default='', help='why this vintage exists, for the document')
    args = ap.parse_args(argv)
    if args.capture:
        return capture(args.capture[0], args.capture[1], args.note)
    if args.sync:
        return sync()
    if args.adopt:
        return adopt()
    return report()


if __name__ == '__main__':                                  # pragma: no cover
    raise SystemExit(main())
