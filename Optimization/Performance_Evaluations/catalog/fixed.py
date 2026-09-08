"""catalog.fixed — what this sweep VARIED, and what it held still.

"Not tested" is a finding, and it was the one part of the published methodology that had no
artifact behind it.  A reader asking "is this sensitive to safety stock?" got silence, which
reads as an oversight rather than as a scope boundary — and a reader asking "what else did
you hold fixed?" had to reconstruct the answer from four config files.

This derives both halves from the run itself.  A factor is VARIED when the run's own tree
carries more than one distinct value of it, and FIXED when it carries exactly one; the
fixed list is therefore complete by construction rather than by an author remembering.  A
factor whose value never appears anywhere — inter-aisle travel, which no scheduler models —
is reported as ABSENT, which is a third and different thing: not held at a value, but not a
knob this model has.

The three-way split is only worth having if the third bucket is policed, and it was not.
SAFETY STOCK sat in `_ABSENT` claiming the plan builder "takes no safety-stock parameter",
which was true of the generator's closed form and stopped being true when ADR-0002 retired
it: a run now declares `safety_days` on `STAFFING_KEYS`, with a `--safety-days` flag, and
derives its reorder point from it.  So it is a knob HELD FIXED, and the correction is
structural rather than a re-worded line — the two families of factor come from two different
records.  `_CONFIG_FACTORS` are per-leaf and read out of each leaf's `config_json`;
`_SPEC_FACTORS` are declared once for the whole run and read out of the run spec's staffing
record.  A factor listed in the wrong family does not merely mis-report: it never appears in
a leaf, so it drops out of the register entirely, which is the shape of the original mistake.

Both records are named by their CONTRACT alias throughout — `config_json`, `run_spec` — and
never by filename: the path is `rt.path`'s business, and a literal spelled even in a comment
is the hand-joined-path debt the run-tree ratchet exists to stop.
"""
import json
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io

#: Leaf-config keys worth reporting, with the plain-language name a page would use.
#: Everything a reader might ask "did you vary that?" about — and ONLY keys a leaf's
#: `config_json` record actually carries
#: (`_prepare_channel_run@Optimization/simdriver/workunits.py` writes that record).  A key
#: that never appears in a leaf contributes no level and vanishes from the register silently,
#: so this list is checked against the writer, not guessed.
_CONFIG_FACTORS = (
    ('n_batches',        'waves per run'),
    ('num_pickers',      'crew size'),
    ('total_aisles',     'aisles'),
    ('total_bins',       'bins'),
    ('n_skus',           'stocked SKUs'),
    ('sampler',          'demand sampler'),
    ('pick_intercept',   'per-pick setup seconds'),
    ('cart_capacity',    'cart capacity'),
    ('cart_swap_coef',   'cart-swap seconds'),
    ('batch_mean_frac',  'wave size, as a share of the catalogue'),
    ('height_brackets',  'height multipliers'),
    # The four whole-catalogue averages the leaf record carries under `catalogue_scope:
    # all_regimes`.  The first two survive ADR-0002 unchanged: the levels left the CATALOGUE,
    # not the run, and `_prepare_channel_run` still averages them off the orders the planner
    # fielded — so they remain a leaf-config value and stay here.  What they mean has changed,
    # and `catalog.inventory` is where the distribution behind these means is published.
    ('avg_equilibrium_qty', 'mean order-up-to quantity'),
    ('avg_reorder_point',   'mean reorder point'),
    ('avg_lead_time_mean',  'mean lead time'),
    ('avg_supply_cv',       'supply variability'),
)

#: Run-level declarations, read from the run spec's staffing record (`staffing.inputs`, one
#: entry per `sim_config.STAFFING_KEYS`) rather than from any leaf.  A sweep declares these
#: ONCE, so each contributes exactly one level and lands in `fixed` under the same rule the
#: leaf factors obey — which is the honest answer to "did you vary the coverage?".
#:
#: The three coverage knobs and no more, deliberately: the rest of `STAFFING_KEYS` is crew
#: sizing and pricing, and `num_pickers` is already a leaf factor above (a second, differently
#: named entry for the same quantity would read as two answers).  They are the run's stock
#: DECLARATION since ADR-0002 — the catalogue authors no level, so `--coverage-days`,
#: `--safety-days` and `--floor-lines` are the whole of what a sweep could have varied here.
_SPEC_FACTORS = (
    ('coverage_days', 'stock coverage, in days of demand'),
    ('safety_days',   'safety stock, in days of demand'),
    ('floor_lines',   "the stock floor, in lines of the SKU's own mean line"),
)

#: Knobs a reader may ask about that this model has no parameter for at all.  Named
#: explicitly, because "we did not vary it" and "there is nothing to vary" are different
#: answers and only one of them is a gap a future sweep could close cheaply.
#:
#: An entry here is a claim about the CODE, so it rots when the code grows the knob.  Safety
#: stock used to head this list; it is now `safety_days` on `_SPEC_FACTORS` — a declared run
#: input with its own flag, held fixed at whatever the run spec records.  Before adding an
#: entry, grep for a settings constant and a flag: an absent knob has neither.
_ABSENT = (
    ('breaks_and_shifts',
     'Breaks, lunches and shift changes WITHIN a working day are not modeled: a crew works '
     'continuously from the moment its day opens until its whistle. The day itself is '
     'modeled — a working-day length, a release cadence, and a receiving crew with hours of '
     'its own — so what is absent is the structure inside a shift, not the shift.'),
    ('inter_aisle_travel',
     'Walking between aisles is not modeled, for either scheduler.'),
    ('aisle_congestion',
     'Several pickers heading for the same aisle is not modeled.'),
    ('cold_start_skus',
     'Every SKU has demand history from batch zero; there is no new-item case.'),
)


#: run_spec keys whose VALUE is a machine-local path.  They are dropped rather than
#: sanitised: this document is committed to the site, and CLAUDE.md's rule is that no
#: tracked file carries a drive letter, a home directory or a username.  The path guard
#: caught exactly this — `profiles_dir` rode into a staged artifact on the first render.
_PATH_KEYS = ('profiles_dir', 'base_dir', 'output_dir', 'inv_db', 'aff_db')


def _publishable_spec(spec: dict) -> dict:
    """The run's shaping scalars, with anything path-shaped removed.

    Scalars only (a nested list is the sweep's own matrix, reported by `varied` instead),
    and no value that looks like a filesystem location — a key name is not enough to go
    on, so the value is checked too.
    """
    out = {}
    for k, v in spec.items():
        if k in _PATH_KEYS:
            continue
        # The staffing record is the ONE nested family (`{inputs, provenance, ...}`); it
        # holds the declared headcount, which is exactly what a reader comparing two
        # experiments needs to see.  Flattened: each input under its own key, its
        # provenance beside it.  The scalar filter below would otherwise drop it whole.
        if k == 'staffing' and isinstance(v, dict):
            prov = v.get('provenance') or {}
            for ik, iv in (v.get('inputs') or {}).items():
                out[ik] = iv
                if ik in prov:
                    out[f'{ik}_provenance'] = prov[ik]
            continue
        if not (isinstance(v, (str, int, float, bool)) or v is None):
            continue
        if isinstance(v, str) and any(c in v for c in ('\\', '/', ':')):
            continue
        out[k] = v
    return out


def _levels(ctx) -> dict:
    """{factor: sorted distinct values} across the whole run — the tree, the leaves, the spec.

    Three sources, one rule.  The tree supplies the sweep's own axes (cell, inventory, pick
    config, channel); each leaf's `config_json` supplies `_CONFIG_FACTORS`; the run spec's
    staffing record supplies `_SPEC_FACTORS`, which no leaf carries.  Whatever the source, a
    factor with one distinct value is FIXED and one with several is VARIED, so a run-level
    declaration is reported exactly as honestly as a per-leaf one.
    """
    import json as _json
    out: dict = {}

    def add(name, value):
        if value is None:
            return
        out.setdefault(name, set()).add(
            _json.dumps(value, sort_keys=True) if isinstance(value, (list, dict))
            else value)

    for cell, cr in ctx.rt.channel_runs():
        add('cell', cell)
        add('inventory', cr.pair)
        add('config', cr.config)
        add('channel', cr.channel)
        try:
            path = ctx.rt.path('config_json', cell=cell, pair=cr.pair, config=cr.config)
        except Exception:                                  # noqa: BLE001 - absence is data
            continue
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            cfg = _json.load(fh)
        for key, _label in _CONFIG_FACTORS:
            add(key, cfg.get(key))

    # The run-level declarations, added ONCE — they are recorded at the run root, not per
    # leaf, so iterating the channel-runs for them would say nothing new and reading them
    # from a leaf's `config_json` (where they do not appear) would drop them entirely.  A run
    # that recorded no staffing block contributes nothing here, the same absence-is-data
    # handling the leaf loop above gives a missing config.
    inputs = (ctx.run_spec().get('staffing') or {}).get('inputs') or {}
    for key, _label in _SPEC_FACTORS:
        add(key, inputs.get(key))
    return {k: sorted(v, key=str) for k, v in out.items()}


def register(ctx) -> dict:
    labels = dict(_CONFIG_FACTORS + _SPEC_FACTORS)
    labels.update(cell='scheduler cell', inventory='inventory model (supply)',
                  config='pick configuration', channel='channel')
    lv = _levels(ctx)
    varied, fixed = [], []
    for name, values in sorted(lv.items()):
        row = {'factor': name, 'label': labels.get(name, name),
               'n_levels': len(values), 'levels': list(values)}
        (varied if len(values) > 1 else fixed).append(row)
    return {
        'note': ('Derived from the run\'s own tree, its leaf configs and its run spec: a '
                 'factor is VARIED when more than one distinct value appears in this run '
                 'and FIXED when exactly one does, so the fixed list cannot be incomplete. '
                 'The run-level declarations (stock coverage, safety stock, the stock '
                 'floor) come from the spec because a sweep states them once; every other '
                 'factor comes from the leaves. ABSENT names knobs the model has no '
                 'parameter for — a different answer from "held constant", and the only '
                 'one that implies a code change to explore.'),
        'spec': _publishable_spec(ctx.run_spec()),
        'varied': varied,
        'fixed': fixed,
        'absent': [{'factor': k, 'why': why} for k, why in _ABSENT],
    }


@evaluation(key='catalog.fixed', label='What the sweep varied, and what it held fixed',
            scope='run', out_subdir='')
def render(ctx, params):
    doc = register(ctx)
    path = os.path.join(io.out_dir(ctx), 'held_fixed.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)
    ctx.log.info(f"  wrote the factor register ({len(doc['varied'])} varied, "
                 f"{len(doc['fixed'])} fixed, {len(doc['absent'])} absent)")
