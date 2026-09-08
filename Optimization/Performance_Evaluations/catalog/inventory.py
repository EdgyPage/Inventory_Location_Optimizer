"""catalog.inventory — the stock levels this run DECLARED, published as a distribution.

WHY THIS EXISTS.  A page has to say how much stock the simulation held, and the honest answer
has never been a formula.  It used to fail to be a formula for a particular reason: the
CATALOGUE authored each SKU's order-up-to quantity and reorder point, the published pages
restated the generator's closed form, and measured against the run's own file that form
reproduced neither quantity — `sample_to_capacity` rescaled both to fit the warehouse after
the planner had spoken.  So this tool scored the candidate closed forms against the stored
values and published the implied rescale beside them, on the principle that "neither formula
holds" should be a measurement rather than an assertion.

Both of those measurements are now about a mechanism that no longer exists.  ADR-0002 retired
the authored level: a stock level is not a fact about a SKU, and a generated catalogue carries
none.  Every run DERIVES its levels at setup from a coverage in DAYS
(`Optimization/simconfig/coverage.py`, driven to a pair-level fixed point by
`Optimization/simdriver/era_coverage.py`) and records them in its own planned inventory.  No
closed form authors a level any more, so a reproduction rate would score the agreement of two
things the run never used, and the planner growth the rescale ratio measured has no generator
figure left to grow away from.  Both blocks are gone.  What remains is the reason the tool
existed in the first place: PUBLISH THE DISTRIBUTION the run actually stocked.

WHAT THIS EMITS.  Per inventory pair: the realised distribution of the three declared levels
(order-up-to quantity, reorder point, lead pipeline) and of the lead time the reorder point is
denominated in, how many SKUs the run declared on, how many carry a hand-written packing plan,
and THE DECLARATION ITSELF — `coverage_days` / `safety_days` / `floor_lines`, read off the
run's own staffing record (`staffing.calibration[<pair>].coverage`), which is where a run
states what produced these numbers.  A page renders the distribution and names the
declaration; change the declaration and both change with it.

WHICH FILE, AND HOW IT IS READ.  The `planned_inventory` contract alias — a RUN'S OWN planned
inventory, never a generated catalogue, because only a run declares.  Read through
`Schema.dataset`'s named queries instead of hand-written SQL against `cartons`: the
`stock_levels` query serves the declaration out of the table that holds it on a current file
and, through its per-vintage override, out of `cartons` on every archived one.  So an old run
still reports the levels it fielded, under the same logical names, with no version branch
here.
"""
import csv
import json
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io

#: The three quantities a run DECLARES, served by the `stock_levels` named query.
#: `pipeline_qty` is NULL on a file whose run predates the stamp; `_dist` drops the Nones,
#: so that pair simply reports a smaller `n` for it rather than a zero-inflated spread.
_LEVEL_FIELDS = ('equilibrium_qty', 'reorder_point', 'pipeline_qty')

#: The SKU's own supply fact, served by the `cartons` named query.  Published beside the
#: levels because the reorder point is demand over (lead + safety) days: without the lead
#: distribution the reorder-point distribution cannot be read.
_CARTON_FIELDS = ('lead_time_mean',)

_FIELDS = _LEVEL_FIELDS + _CARTON_FIELDS

#: The CSV's columns, declared rather than taken from the first row — an empty run must still
#: write a header (see `render`), and `_dist`'s keys are the contract a table consumer binds
#: to, not an accident of whichever pair happened to be first.
_CSV_FIELDS = ('pair', 'quantity', 'n', 'mean', 'median',
               'p5', 'p25', 'p75', 'p95', 'min', 'max')


#: Every declared-shape column this tool touches, and HOW (phase-2 of the staged
#: semantics gate).  A PURE LITERAL: Tests/architecture/test_column_semantics.py
#: AST-reads it and validates against Schema/semantics.py without importing this
#: module, so declaring costs no dependency.
#:
#: The levels are named in `stock_levels`, the table they LIVE in since ADR-0002 — not in
#: `cartons`, where a pre-split vintage physically keeps them.  That is the point of naming
#: logical reads: the override that reads an archived file's `cartons` is the schema layer's
#: business, and this declaration states what the tool asks for, not where a given file
#: happens to answer from.
SEMANTIC_USES = {'inventory_db': {
    'stock_levels.equilibrium_qty': 'read', 'stock_levels.reorder_point': 'read',
    'stock_levels.pipeline_qty': 'read', 'stock_levels.stock_plan': 'read',
    'cartons.lead_time_mean': 'read',
}}


def _dist(vals: list) -> dict:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return {}
    n = len(vals)

    def q(p):
        return float(vals[min(n - 1, max(0, int(round(p * (n - 1)))))])
    return {'n': n, 'mean': sum(vals) / n, 'median': q(0.5),
            'p5': q(0.05), 'p25': q(0.25), 'p75': q(0.75), 'p95': q(0.95),
            'min': float(vals[0]), 'max': float(vals[-1])}


# ── the read ─────────────────────────────────────────────────────────────────────
def pair_model(db_path: str) -> dict:
    """Realised distributions for one pair's planned inventory, read by LOGICAL name.

    `dataset.bind` resolves the file to its OWN vintage and refuses one the family does not
    vet — deliberately left to raise.  This path publishes: a level read out of a shape
    nobody has vetted is exactly the plausible-wrong-number the schema layer exists to stop,
    and a run's pairs are all written by one build, so catching per pair would rescue
    nothing anyway.

    A pair that declares NOTHING is not an error and does not raise: `stock_levels` comes
    back empty (a generated catalogue frozen without a derivation, or a rebuild from an
    inventory that carried none), the level distributions are `{}`, and `n_declared` says so.

    No `requires=` is passed, deliberately.  A `Requires` names PHYSICAL tables and would
    refuse every pre-split vintage — whose `stock_levels` table does not exist, because its
    override reads the declaration out of `cartons` — which is exactly the file this tool
    most needs to read.  The named-query registry is the guarantee here: `UnsupportedQuery`
    names the (vintage, query) pair when no SQL can serve it, by name and before any row.
    """
    # Imported HERE rather than at module scope for two reasons.  The import is what
    # REGISTERS the `inventory_db` family and its two named queries, so `bind`/`query` cannot
    # resolve without it — and `generate_inventory` is a data-gen CLI that drags matplotlib
    # and pandas, which every preset that loads the evaluation registry would otherwise pay
    # for whether or not a run-scope dossier is being rendered.
    from Schema import dataset as _dataset
    import Warehouse.generation.generate_inventory        # noqa: F401  (registers the reads)

    # `limit=-1` is SQLite's "no limit"; both named queries take it and both order by sku.
    # Read-only, NOT immutable: `load_inventory_from_db` binds the same file the same way,
    # and an immutable open sees only the checkpointed page image — a promise this tool is
    # in no position to make about a tree an analysis pass may still be writing beside.
    with _dataset.bind(db_path, 'inventory_db') as ds:
        cartons = ds.query('cartons', limit=-1)
        levels = ds.query('stock_levels', limit=-1)

    by_field = {f: [r.get(f) for r in levels] for f in _LEVEL_FIELDS}
    by_field.update({f: [r.get(f) for r in cartons] for f in _CARTON_FIELDS})
    return {
        'n_skus': len(cartons),
        # Declared vs stocked, as two numbers rather than one: a run declares on the orders
        # its planner fielded, so the gap (when there is one) is the SKUs the plan left
        # without a level, not a dropped read.
        'n_declared': len(levels),
        # A planned SKU OVERRIDES the pallet/singleton packing rule, which is why splitting a
        # delivery can produce fewer units.  A count, not a distribution: `stock_plan` is a
        # JSON run-length label, and how many SKUs bypass the packer is the question a reader
        # of the level distribution actually has.
        'n_stock_plans': sum(1 for r in levels if r.get('stock_plan')),
        'distributions': {f: _dist(by_field[f]) for f in _FIELDS},
    }


def _declaration(ctx, pair: str) -> dict:
    """The coverage this run DECLARED for `pair` — the three knobs, off the staffing record.

    The run states its own inputs; this never re-derives them.  Read defensively because an
    absent declaration is DATA: a run whose assets were rebuilt from a frozen inventory, or
    one that predates the coverage loop, records no `coverage` block, and the right report for
    it is "the file's levels, declaration unrecorded" rather than a raise or an invented
    default.
    """
    staffing = ctx.run_spec().get('staffing') or {}
    cal = (staffing.get('calibration') or {}).get(pair) or {}
    cov = cal.get('coverage') or {}
    return {k: cov[k] for k in ('coverage_days', 'safety_days', 'floor_lines') if k in cov}


# Two homes, declared rather than improvised: the JSON is a dossier document a macro
# loads by name, the CSV belongs beside the other tables.  A tuple out_subdir is the
# registry's mechanism for exactly this, and `io.out_dir(ctx, pick=...)` must name a
# declared member — so neither file can drift into a directory nothing declares.
@evaluation(key='catalog.inventory', label='The stock levels this run declared',
            scope='run', needs=('catalogue',), out_subdir=('', 'tables'))
def render(ctx, params):
    pairs = ctx.catalogue_dbs()
    if not pairs:
        return
    doc = {'note': ("Measured from the run's own planned inventory — the levels this run "
                    'DECLARED at setup and its workers reloaded, not a generator parameter, '
                    'not a config average, and not a closed form. Since ADR-0002 the '
                    'catalogue authors no level: every run derives its own from a coverage '
                    'in DAYS, so these distributions are the whole of the model.'),
           'source': 'planned_inventory@run tree (contract alias)',
           'declared_by': ("run_spec staffing.calibration[<pair>].coverage — the declared "
                           'coverage_days / safety_days / floor_lines, the fixed point\'s '
                           'rounds, and the share of SKUs sitting on the line floor. The '
                           'three knobs are repeated per pair below; the loop\'s own record '
                           'is the place to read the rest.'),
           'pairs': {}}
    for pair, path in pairs:
        m = pair_model(path)
        m['declaration'] = _declaration(ctx, pair)
        doc['pairs'][pair] = m

    root = io.out_dir(ctx, pick='')
    with open(os.path.join(root, 'inventory_model.json'), 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)

    # Header first, rows second, and unconditionally: a run whose pairs declare nothing must
    # leave a table with no rows, which is a readable "it declared nothing", rather than an
    # IndexError off `flat[0]` that leaves the JSON written and the CSV absent.
    tdir = io.out_dir(ctx, pick='tables')
    with open(os.path.join(tdir, 'inventory_model.csv'), 'w', newline='',
              encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for pair, m in doc['pairs'].items():
            for field, d in m['distributions'].items():
                if d:
                    w.writerow({'pair': pair, 'quantity': field, **d})

    for pair, m in doc['pairs'].items():
        d = m['declaration']
        decl = (f"{d['coverage_days']:g} d coverage + {d['safety_days']:g} d safety, "
                f"floor {d['floor_lines']:g} line(s)"
                if len(d) == 3 else 'declaration unrecorded')
        eq = m['distributions'].get('equilibrium_qty') or {}
        ctx.log.info(f"  inventory model {pair}: {m['n_declared']:,} of {m['n_skus']:,} SKUs "
                     f"declared ({decl}); median order-up-to "
                     f"{eq.get('median', float('nan')):,.1f} units")
