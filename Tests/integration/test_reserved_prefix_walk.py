"""test_reserved_prefix_walk.py — the reserved prefix is DECLARED once and honoured at every depth.

`runschema/schema.py:RESERVED_PREFIX` is the run-tree contract's statement that a leading
underscore names a driver bookkeeping subtree, never an axis value.  The tree walkers used to
retype it as a literal and test it at the PAIR level only, so a reserved dir one level deeper
walked as a phantom config (`<pair>/_site/` — the coupled unit's site DB lives there) and any
`sim_*.db` beneath it surfaced as an extra ARM of that phantom.

Both failure modes are silent: nothing raises, the run simply grows a config and an arm that
were never simulated.  These tests plant a reserved dir at each depth and assert the walkers
see nothing.

Run:  python -m pytest Tests/integration/test_reserved_prefix_walk.py -q
"""
from __future__ import annotations

import json
import os

from Optimization.runschema.schema import RESERVED_PREFIX
from Optimization.runschema.runlayout import iter_channel_runs, iter_sim_dbs


def _leaf(path: str, marker: bool = True, arms=('uni_rank_labor',)) -> None:
    """A channel-run leaf: sim_meta.json plus one sim DB per arm."""
    os.makedirs(path, exist_ok=True)
    if marker:
        with open(os.path.join(path, 'sim_meta.json'), 'w', encoding='utf-8') as fh:
            json.dump({'strategies': []}, fh)
    for arm in arms:
        open(os.path.join(path, f'sim_{arm}.db'), 'wb').close()


def _tree(base) -> str:
    """<cell>/<pair>/<config>[/<channel>] with a reserved dir planted at all THREE depths."""
    cell = str(base)
    _leaf(os.path.join(cell, 'pairA', 'store'))                       # store-only leaf
    _leaf(os.path.join(cell, 'pairA', 'ful_calibrated', 'fulfillment'))
    # the three plants — each a full, marker-carrying, sim-DB-bearing leaf, so ONLY the
    # prefix can be what excludes it
    _leaf(os.path.join(cell, RESERVED_PREFIX + 'aggregate', 'store'))          # pair depth
    _leaf(os.path.join(cell, 'pairA', RESERVED_PREFIX + 'site'))               # config depth
    _leaf(os.path.join(cell, 'pairA', 'ful_calibrated',
                       RESERVED_PREFIX + 'viz'))                               # channel depth
    return cell


def test_reserved_dirs_yield_no_channel_run(tmp_path):
    cell = _tree(tmp_path)
    seen = [(r.pair, r.config, r.channel) for r in iter_channel_runs(cell)]
    assert seen == [('pairA', 'ful_calibrated', 'fulfillment'), ('pairA', 'store', None)]


def test_reserved_dirs_yield_no_sim_db(tmp_path):
    cell = _tree(tmp_path)
    rels = sorted('/'.join(os.path.relpath(db, cell).split(os.sep)) for _r, db in iter_sim_dbs(cell))
    assert rels == ['pairA/ful_calibrated/fulfillment/sim_uni_rank_labor.db',
                    'pairA/store/sim_uni_rank_labor.db']


def test_the_plants_are_real_leaves(tmp_path):
    """Non-vacuity: rename the prefix off each plant and the walkers DO pick it up — so the
    two assertions above are excluding them by prefix, not by a malformed fixture."""
    cell = _tree(tmp_path)
    for old, new in ((os.path.join(cell, 'pairA', RESERVED_PREFIX + 'site'),
                      os.path.join(cell, 'pairA', 'zsite')),
                     (os.path.join(cell, 'pairA', 'ful_calibrated', RESERVED_PREFIX + 'viz'),
                      os.path.join(cell, 'pairA', 'ful_calibrated', 'zviz'))):
        os.rename(old, new)
    seen = {(r.pair, r.config, r.channel) for r in iter_channel_runs(cell)}
    assert ('pairA', 'zsite', None) in seen
    assert ('pairA', 'ful_calibrated', 'zviz') in seen
    assert len(list(iter_sim_dbs(cell))) == 4
