"""catalog.fixed — what this sweep VARIED, and what it held still.

"Not tested" is a finding, and it was the one part of the published methodology that had no
artifact behind it.  A reader asking "is this sensitive to safety stock?" got silence, which
reads as an oversight rather than as a scope boundary — and a reader asking "what else did
you hold fixed?" had to reconstruct the answer from four config files.

This derives both halves from the run itself.  A factor is VARIED when the run's own tree
carries more than one distinct value of it, and FIXED when it carries exactly one; the
fixed list is therefore complete by construction rather than by an author remembering.  A
factor whose value never appears anywhere — safety stock on the plan builder, which has no
such parameter — is reported as ABSENT, which is a third and different thing: not held at a
value, but not a knob this model has.
"""
import json
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io

#: Leaf-config keys worth reporting, with the plain-language name a page would use.
#: Everything a reader might ask "did you vary that?" about.
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
    ('avg_equilibrium_qty', 'mean equilibrium quantity'),
    ('avg_reorder_point',   'mean reorder point'),
    ('avg_lead_time_mean',  'mean lead time'),
    ('avg_supply_cv',       'supply variability'),
)

#: Knobs a reader may ask about that this model has no parameter for at all.  Named
#: explicitly, because "we did not vary it" and "there is nothing to vary" are different
#: answers and only one of them is a gap a future sweep could close cheaply.
_ABSENT = (
    ('safety_stock',
     'The plan builder this run used derives the reorder point from lead time alone; it '
     'takes no safety-stock parameter, so there is no value to hold fixed. Varying it '
     'means changing the builder signature, not the run spec.'),
    ('breaks_and_shifts',
     'The model has no shift structure: no breaks, lunches or shift changes.'),
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
        if not (isinstance(v, (str, int, float, bool)) or v is None):
            continue
        if isinstance(v, str) and any(c in v for c in ('\\', '/', ':')):
            continue
        out[k] = v
    return out


def _levels(ctx) -> dict:
    """{factor: sorted distinct values} across the whole run, from the tree and the leaves."""
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
    return {k: sorted(v, key=str) for k, v in out.items()}


def register(ctx) -> dict:
    labels = dict(_CONFIG_FACTORS)
    labels.update(cell='scheduler cell', inventory='inventory model (supply)',
                  config='pick configuration', channel='channel')
    lv = _levels(ctx)
    varied, fixed = [], []
    for name, values in sorted(lv.items()):
        row = {'factor': name, 'label': labels.get(name, name),
               'n_levels': len(values), 'levels': list(values)}
        (varied if len(values) > 1 else fixed).append(row)
    return {
        'note': ('Derived from the run\'s own tree and leaf configs: a factor is VARIED '
                 'when more than one distinct value appears in this run and FIXED when '
                 'exactly one does, so the fixed list cannot be incomplete. ABSENT names '
                 'knobs the model has no parameter for — a different answer from "held '
                 'constant", and the only one that implies a code change to explore.'),
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
