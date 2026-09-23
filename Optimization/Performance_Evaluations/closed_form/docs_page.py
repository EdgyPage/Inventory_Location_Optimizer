"""docs_page — `docs/closed-form-models.md`, written by the models themselves.

Every equation on the page is `Equation.latex` of the tree the simulator's mirror tests hold
equal to the code, and every worked number is `Model.evaluate` of the inputs listed here -- so
the page cannot say something the models do not compute.  `Tests/unit/test_closed_form_render.py`
regenerates the Markdown and fails when the committed page differs: edit a model, then run

    python -m Optimization.Performance_Evaluations.closed_form.docs_page --write

(`--check` exits 1 on drift; `--write` also redraws the page's figures under
`docs/images/closed-form/`, which are not byte-reproducible and so are not part of the check).
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
PAGE = os.path.join(_REPO, 'docs', 'closed-form-models.md')
FIG_DIR = os.path.join(_REPO, 'docs', 'images', 'closed-form')

INTRO = """
Closed forms for what the simulator charges and why: where a SKU's stock level comes from,
when it reorders, what one pick costs, how much of the picking an inbound decision can reach,
and how long a placement rule keeps finding good bins.  **This page is generated** from
`Warehouse/kernel/closed_form.py` and `Optimization/simconfig/models/` -- each equation below
is printed from the same expression tree that computes it, and the equations marked
_mirrors_ are held equal to the simulator code they name by `Tests/unit/test_closed_form.py`.
The worked numbers are the reference 40k catalogue's store section at the declared demand;
the study that verified each law against the simulator is `.scratch/aisle-churn/` (S00-S13).

![levels dependency graph](images/closed-form/levels_graph.png)
"""

FIGURES_NOTE = """
## The fresh-bin share over demand density

![fresh-bin share](images/closed-form/fresh_share.png)

A SKU drawn once in a window has no line an inbound decision could serve; the share climbs
with the line rate because a later line of the same SKU drains the fresh packs first
(smallest on hand, ADR-0003).  Demand density is therefore the lever that lets an inbound
decision reach the picking: 8% of the store's picks at the declared demand, ~70% at 30x.
"""


def _store_pick_law():
    from Optimization.config.sim_config import _build_pick_cfg
    from Optimization.simconfig import PICK_CONFIGS
    spec = next(s for s in PICK_CONFIGS if s.name == 'store')
    return _build_pick_cfg(spec.cfg, num_pickers=1).closed_form


def sections():
    """`[(model, result, heading), ...]` in page order."""
    from Optimization.simconfig.models import churn, dock, levels, reorders
    law = _store_pick_law()
    n_store, skus = 58.585998, 23_880
    return [
        (law.model, law.result(y=114.0, w=20.0, vol=1.5, q=3), 'What one pick costs (store)'),
        (levels.LEVELS, levels.LEVELS.evaluate({
            'lam': 10.0, 'n': n_store, 'pi': 1.0 / skus, 'f_L': 1.3078, 'supplier': 0.0,
            'unit': 1.0, 'transit': 1.766, 'C': 10.0, 'safety': 2.0}),
         'Where a SKU\'s stock level comes from'),
        (reorders.REORDERS, reorders.REORDERS.evaluate({
            'lam': 10.0, 'P': 0, 'p': n_store / skus, 'cv': 0.0}), 'When it reorders'),
        (churn.FRESH, churn.FRESH.evaluate({'lam': n_store / skus, 'H': 40.0, 'ell': 2.766}),
         'How much picking an inbound decision reaches'),
        (churn.STEADY, churn.STEADY.evaluate({
            'n_b': 248_350, 'nu_b': 1.0, 'turn': 1_020_434.0, 'G_free': 36_766.0,
            'm': 1_774.0, 's': 0.641, 'sigma_G': 0.243}),
         'How long a ranked rule keeps finding good bins'),
        (churn.GAP_CHAIN, churn.GAP_CHAIN.evaluate({
            'phi': 0.086, 'U': 258_930.0, 'hbar': 59.0, 'M_rank': 1.162, 'M_free': 1.263,
            'T': 26_845_232.0}), 'What the placement rule is worth on the picks'),
        (dock.DOCK, dock.DOCK.evaluate({
            'lam_T': 28.0, 'W_T': 34_500.0, 'team': 10, 'overhead': 0.08, 'doors': 4,
            'S': 28_800.0}), 'When the dock saturates'),
        (dock.AISLE, dock.AISLE.evaluate({'S': 28_800.0, 'W_max': 2_251.0}),
         'When picking stops keeping up'),
    ]


def render() -> str:
    """The page's Markdown, deterministic."""
    out = ['# Closed-form models', '', INTRO.strip(), '']
    for model, result, heading in sections():
        out += [model.to_markdown(result, title=heading), '']
    out += [FIGURES_NOTE.strip(), '']
    return '\n'.join(out).rstrip() + '\n'


def draw_figures():
    from Optimization.Performance_Evaluations.closed_form import render as r
    from Optimization.simconfig.models import churn, levels
    os.makedirs(FIG_DIR, exist_ok=True)
    r.model_graph(levels.LEVELS, os.path.join(FIG_DIR, 'levels_graph.png'),
                  title='Where a SKU\'s stock level comes from')
    r.sweep_chart(churn.FRESH, 'lam', [0.0005 * 1.5 ** i for i in range(20)],
                  {'H': 40.0, 'ell': 2.766}, ['phi'], os.path.join(FIG_DIR, 'fresh_share.png'),
                  logx=True, scale=100.0, xlabel='lines per day of the SKU',
                  ylabel='lines an inbound decision reaches, %',
                  title='Fresh-bin share over a 40-day window',
                  subtitle='lead 2.77 days; the declared store SKU draws 0.0025 lines a day')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--write', action='store_true')
    g.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    text = render()
    if a.check:
        cur = open(PAGE, encoding='utf-8').read() if os.path.exists(PAGE) else ''
        if cur != text:
            print(f'{os.path.relpath(PAGE, _REPO)} is stale: run --write')
            return 1
        return 0
    with open(PAGE, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    draw_figures()
    print(f'wrote {os.path.relpath(PAGE, _REPO)} and {os.path.relpath(FIG_DIR, _REPO)}/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
