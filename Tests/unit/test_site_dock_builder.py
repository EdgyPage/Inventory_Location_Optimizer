"""test_site_dock_builder.py — the SITE dock at unit scope, and the four refusals.

Site-dock 24.  `_build_site_dock` is the receiving twin of `_build_put_pool`: it mints the
one dock, the one yard and the one receiving roster a coupled unit fields, above both
leaves, because that is the only scope that can see both.  What is checked here is the
shape of its answer and the four run shapes it REFUSES — each of which, degraded instead
of refused, is the double count with a coupled label on it.

A HAND-BUILT PAYLOAD, not a prepared run: the builder reads dict keys and the put pool's
roster and nothing else, so a real pair would cost two minutes of simulation to assert
facts about a dict.  The end-to-end wiring — that the dock the builder returns is the dock
both leaves actually bind, and that its price list is the two constants an uncoupled leaf
already charges — is `Tests/e2e/test_coupled_unit_e2e.py`'s
`test_a_coupled_site_dock_matches_the_two_docks_it_replaces`, which runs the real seam.

Run:  python -m pytest Tests/unit/test_site_dock_builder.py -q
"""
from __future__ import annotations

import logging
import os

import pytest

from Optimization.simdriver import strategy_runner as sr
from Optimization.config.sim_config import REGRESSION_CONFIGS, _build_pick_cfg

_LOG = logging.getLogger('site-dock-builder')

#: The standing-yard record both leaves carry.  `inbound_spec()` is ONE site-wide accessor,
#: so on a lawful run these two dicts are equal — which is exactly what the builder checks.
_INBOUND = {
    'standing': True, 'trailer_type': '28', 'lead_s': 0.0, 'lead_sigma': 0.0,
    'lead_seed': 7, 'doors': 2, 'yard_policy': 'fifo', 'dock_policy': 'fifo',
    'local_policy': 'fifo', 'bound': None, 'allocation': 'merged', 'door_team': None,
    'unload_intercept': None, 'unload_weight_coef': None, 'unload_volume_coef': None,
}

_PUT_CREW = {'size': 2, 'mode': 'foot', 'x_speed': 2.0, 'y_speed': 4.0}
_RECV_CREW = {'size': 2, 'mode': 'foot', 'x_speed': 2.0, 'y_speed': 4.0}


def _leaf(channel: str, regime: str, k_pickers: int, inbound=_INBOUND) -> dict:
    """One leaf payload, carrying only what the two unit-scope builders read.

    `channel_name` and `channel_regime` are deliberately DIFFERENT fields — `Channel` keeps
    them independent — and the dock's price list must be keyed by the REGIME, because
    `Dock.unload_seconds` resolves with `regime_of(unit)`.
    """
    return {
        'channel_name': channel, 'channel_regime': regime, 'k_pickers': k_pickers,
        # A REAL `PickConfig`, built the way the payload builder builds one: the price
        # list is derived from it through the same picking -> put-away -> receiving
        # chain, so a dict here would exercise nothing the driver actually does.
        'pick_cfg': _build_pick_cfg(REGRESSION_CONFIGS[0], num_pickers=k_pickers),
        'inbound': None if inbound is None else dict(inbound),
        'work_day': {'seconds': 28800.0, 'releases_per_day': 1, 'cut_at_day_end': True},
        'shift_seconds': 28800.0, 'crew_cost': {},
    }


def _unit(*, leaves=None, recv=_RECV_CREW, site_db='X/_site/inbound_a__b.db') -> dict:
    if leaves is None:
        leaves = [_leaf('store', 'store', 25), _leaf('fulfillment', 'fulfillment', 4)]
    unit = {
        'leaves': leaves, 'put_crew': _PUT_CREW,
        'staffing': {'derived': {'put': {'crew': 2,
                                         'expected_utilization': {'store': 0.25,
                                                                  'fulfillment': 0.75}},
                                 'receiving': {'crew': 2}}},
    }
    if recv is not None:
        unit['recv_crew'] = dict(recv)
    if site_db is not None:
        unit['site_db'] = site_db
    return unit


def _built(unit=None):
    unit = unit or _unit()
    pool = sr._build_put_pool(unit)
    return sr._build_site_dock(unit, pool, _LOG), pool


# ── 1. what the builder returns ───────────────────────────────────────────────────

def test_the_site_fields_one_dock_one_yard_and_one_coordinator():
    """ONE of each, where an uncoupled pair fields two: that IS the double count this
    coupling exists to remove, and it is the whole comparability break of site-dock 24."""
    site, _pool = _built()
    assert site is not None
    assert site.dock is site.coord.dock
    assert site.transit is site.coord.transit
    assert site.dock.crew_size == _RECV_CREW['size'], (
        f'the site dock crew is {site.dock.crew_size}; uncoupled this pair fields '
        f'{2 * _RECV_CREW["size"]} receivers for a record that derives '
        f'{_RECV_CREW["size"]}')
    assert getattr(site.transit, 'STANDING', False)


def test_the_price_list_is_keyed_by_regime_and_not_by_channel_name():
    """`Dock.unload_seconds` resolves with `regime_of(unit)`, and `Channel.name` and
    `Channel.regime` are INDEPENDENT fields — so a list keyed by name would price correctly
    only on a site whose channels happen to be named after their regimes."""
    leaves = [_leaf('retail', 'store', 25), _leaf('ecom', 'fulfillment', 4)]
    unit = _unit(leaves=leaves)
    # The PUT POOL keys its day-shares by channel NAME and that is legitimate: a pool tag
    # is a LABEL, not a dispatch key. Only the dock's price list is resolved by regime, so
    # only it has to be keyed by one.
    unit['staffing']['derived']['put']['expected_utilization'] = {'retail': 0.25,
                                                                  'ecom': 0.75}
    site, _pool = _built(unit)
    assert sorted(site.dock.costs) == ['fulfillment', 'store'], site.dock.costs
    assert site.dock.cost is None, 'a site dock holds a LIST and no single price'


def test_each_entry_is_that_channels_own_derived_unload_cost():
    """The two constants that exist today ARE the list's two entries (site-dock 27), which
    is why the pricing decision costs no comparability break: an uncoupled leaf keeps the
    price it has always had, re-derived here from the same inputs."""
    unit = _unit()
    site, _pool = _built(unit)
    for la in unit['leaves']:
        assert site.dock.cost_for(la['channel_regime']) == sr._site_unload_cost(la)


def test_the_receiving_block_chains_off_the_put_pools_block_end():
    """Site-dock 19 rule 2.  The pool's block starts above BOTH channels' dense picker
    uids, so a cursor left at one leaf's own `k_pickers + put_size` would put the smaller
    leaf's receivers INSIDE the putters' block — two crews merged in one DB, silently."""
    site, pool = _built()
    first = site.workers[0].uid
    assert first == pool.workers[-1].uid + 1
    # and the block sits above BOTH channels' pickers, not just the smaller leaf's
    assert first > max(la['k_pickers'] for la in _unit()['leaves'])


def test_an_inbound_off_coupled_unit_fields_no_site_dock():
    """The coupled INBOUND-OFF pole, which the funnel runs in EVERY cell (site-dock 06):
    no trailer pipeline is named, so each leaf keeps its own batch lead queue
    byte-identically. A real configuration, so a return rather than a refusal."""
    leaves = [_leaf('store', 'store', 25, inbound=None),
              _leaf('fulfillment', 'fulfillment', 4, inbound=None)]
    site, _pool = _built(_unit(leaves=leaves))
    assert site is None


# ── 2. the four refusals ──────────────────────────────────────────────────────────

def test_a_non_standing_pipeline_is_refused():
    """The v1 drain runs through each manager's OWN dock deque, so two leaves would field
    two docks and two crews under the one crew the record derives — and no table would
    say so."""
    leaves = [_leaf('store', 'store', 25, inbound={**_INBOUND, 'standing': False}),
              _leaf('fulfillment', 'fulfillment', 4,
                    inbound={**_INBOUND, 'standing': False})]
    with pytest.raises(ValueError, match='STANDING yard'):
        _built(_unit(leaves=leaves))


def test_a_standing_yard_with_no_receiving_crew_is_refused():
    """A yard nobody unloads produces no receipts at all, for the whole run, silently."""
    with pytest.raises(ValueError, match='NO receiving crew'):
        _built(_unit(recv=None))


def test_two_leaves_with_different_inbound_records_are_refused():
    """One site is one yard, one door set and one arrival calendar; two specs would rank
    the same trailers against two different policies."""
    leaves = [_leaf('store', 'store', 25),
              _leaf('fulfillment', 'fulfillment', 4,
                    inbound={**_INBOUND, 'doors': 5})]
    with pytest.raises(ValueError, match='different inbound records'):
        _built(_unit(leaves=leaves))


def test_one_leaf_with_inbound_beside_one_without_is_refused():
    """Not the same failure as the one above, and it has to be its own message: a channel
    with no inbound beside a channel with one is two different warehouses under one run
    label, not two policies over one yard."""
    leaves = [_leaf('store', 'store', 25),
              _leaf('fulfillment', 'fulfillment', 4, inbound=None)]
    with pytest.raises(ValueError, match='name a trailer pipeline and some do not'):
        _built(_unit(leaves=leaves))


def test_a_unit_with_no_site_db_is_refused():
    """The trailer and drain rows belong to neither leaf (ADR-0005); a run that produced
    them with nowhere to put them would report an empty yard on BOTH channels while the
    site was full."""
    with pytest.raises(ValueError, match='names no `site_db`'):
        _built(_unit(site_db=None))


# ── 3. the gain gate is all-or-none ───────────────────────────────────────────────

def _gain_leaf(channel, regime, policy):
    return _leaf(channel, regime, 8, inbound={**_INBOUND, 'yard_policy': policy})


def test_an_asymmetric_gain_policy_is_refused_at_build_time():
    """UNREACHABLE either way — `inbound_spec()` is one site-wide accessor, so both leaves
    carry identical policies with or without the site dock.  What the shared transit changes
    is the CONSEQUENCE: uncoupled a one-owner composite sits inertly on a leaf that only
    ever sees its own regime's keys; under ONE transit the first MIXED trailer reaches
    `for_key` with the unowned regime and raises inside the batch loop, discarding an arm
    hours in."""
    unit = _unit(leaves=[_gain_leaf('store', 'store', 'gain_gated'),
                         _gain_leaf('fulfillment', 'fulfillment', 'fifo')])
    with pytest.raises(ValueError, match='name a gain policy and some do not'):
        sr._build_site_gain(unit)


def test_a_symmetric_gain_policy_builds_one_composite():
    unit = _unit(leaves=[_gain_leaf('store', 'store', 'gain_gated'),
                         _gain_leaf('fulfillment', 'fulfillment', 'gain_gated')])
    made = sr._build_site_gain(unit)
    assert made is not None and made.owners == ()


def test_no_gain_policy_anywhere_builds_nothing():
    unit = _unit(leaves=[_gain_leaf('store', 'store', 'fifo'),
                         _gain_leaf('fulfillment', 'fulfillment', 'fifo')])
    assert sr._build_site_gain(unit) is None

# ── 4. the site DB refuses a SECOND run ───────────────────────────────────────────

def test_a_site_db_that_already_holds_a_run_refuses_a_second(tmp_path):
    """The guard `_plan_strategy_start` keeps over a leaf DB, kept here over the site's.

    `find_run` resolves `ORDER BY run_id LIMIT 1` -- the OLDEST run -- so a file that
    acquires a second one answers every run_id-filtered query from the ABANDONED run and
    doubles every unfiltered aggregate, with no symptom at all.  Reachable: a coupled pair
    killed mid-flight with BOTH leaves at the same batch is in step, so the reconciler used
    to leave its site DB alone while the planner replayed both leaves from batch 0.
    """
    from Optimization.persistence import Picking_Data as pdata
    db = str(tmp_path / '_site' / 'inbound_a__b.db')
    os.makedirs(os.path.dirname(db), exist_ok=True)
    pdata.init_run_db(db)
    pdata.create_run(db, 'site', {})
    unit = _unit(site_db=db)
    pool = sr._build_put_pool(unit)
    site = sr._build_site_dock(unit, pool, _LOG)
    site._yd.append((0, 1, 2, 3, 4))          # something to write, or finish() returns early
    with pytest.raises(RuntimeError, match='already holds run'):
        site.finish()


def test_the_site_run_is_stamped_with_its_arm_pair(tmp_path):
    """Stamped, so the analysis resolves it BY NAME rather than by ordinal -- the same
    rename-proof lookup every leaf DB gets, and the thing that makes a second run in the
    file detectable rather than merely present."""
    from Optimization.persistence import Picking_Data as pdata
    db = str(tmp_path / '_site' / 'inbound_uni_fifo__opt_lpt.db')
    unit = _unit(site_db=db)
    pool = sr._build_put_pool(unit)
    site = sr._build_site_dock(unit, pool, _LOG)
    assert site.arm_pair == 'uni_fifo__opt_lpt'
    site._yd.append((0, 1, 2, 3, 4))
    site.finish()
    import sqlite3
    con = sqlite3.connect(db)
    try:
        stamped = con.execute(
            'SELECT strategy_key FROM simulation_runs').fetchone()[0]
    finally:
        con.close()
    assert stamped == 'uni_fifo__opt_lpt', (
        f'the site run stamped {stamped!r}; unstamped, `find_run` can only resolve it by'
        f' ORDINAL, which is the lookup that reads an ABANDONED run as the current one')
    assert pdata.find_run(db, 'uni_fifo__opt_lpt') == pdata.find_run(db)
