"""test_receiving_report_pair.py — the PAIR-scope half of the receiving reconciler.

`reconcile` answers about ONE sim DB.  Site-dock 15 held four claims back because none of
them is stateable from one file, and site-dock 25 builds them as `reconcile_pair`.  Every
test here plants the defect the clause exists for, because a check that can only pass proves
nothing (memory `conservation-ledger-is-bin-only`) — and the clean-pair test asserts
NON-VACUITY FIRST, which is what site-dock 17 found the hard way when a fixture of seven
identical packs made check 6 green however the price had been computed.

  A. **No uid is a site crew member in one leaf and a picker in the other.**  A uid is not
     an identity; `(role, uid)` is.  The planted defect is site-dock 04's, exactly: a leaf
     that builds its put crew off its OWN pick cursor lands the smaller channel's putter
     inside the larger channel's picker block, and every per-actor rollup then merges two
     people with no symptom anywhere.
  B. **Every site-role uid sits above BOTH leaves' pick blocks** — a floor, never set
     equality, because the smaller leaf is left a deliberate uid gap (site-dock 19) and an
     idle putter writes no rows at all.
  C. **The site dock's own per-batch total closes against the two leaves' rows.**  Two
     accumulators on different code: the dock charges at the charge site, the leaves carry
     per-row durations.  Three planted leaks here, and the third is the one no per-leaf
     check can see — a batch whose rows never reached EITHER leaf's DB, which leaves check 1
     comparing two equally-short surfaces and passing.
  D. **The two constants are per REGIME** (check 6's cross-leaf half, in the two-constant
     form site-dock 27 chose when it retired `C_store == C_ful`).  The sharpest test in this
     file is `test_a_whole_leaf_charged_at_the_other_regimes_rate_fails`: it moves EVERY
     fulfillment row onto the store constant, which leaves both leaves' spreads at zero and
     the cross-price count at zero — the per-leaf check 6 is blind to it in both leaves at
     once — and it is caught only because the two channels' recorded PICK constants still
     differ and a positive linear map cannot send two different inputs to one output.

The DBs are real — `init_run_db` / `create_run` / `save_work_events` / `save_sku_scores` /
`save_site_inbound` under `tmp_path` — because the whole point of this tool is that it reads
a FILE back.  The numbers are the archive's: store prices at C = 7.6 s and fulfillment at
C = 5.1 s (site-dock 17 derived both independently from each channel's own pick config), and
the two pick intercepts are 15 s and 10 s.

Run:  python -m pytest Tests/unit/test_receiving_report_pair.py -q
"""
from __future__ import annotations

import glob
import os
import sqlite3

import pytest

from Diagnostics import receiving_report as rr
from Optimization.persistence import Picking_Data as pdata
from Optimization.runschema.resolver import RunTree

# ── the pair, in numbers ──────────────────────────────────────────────────────────
#: Each channel's unload constant `C = per_item + intercept`, in seconds.  MEASURED values
#: from the archive, not invented ones: site-dock 17 derived both from each channel's own
#: pick config and check 6 holds them to 4.4e-15 over 505,177-row arms.
_C = {'store': 7.6, 'fulfillment': 5.1}

#: Each channel's PICK constant `pick_intercept + pick_per_item`, which is exactly what
#: `sku_scores.labor_cost - handle_var` recovers (`Order.compute_labor_cost`).  It is what
#: clause D's whole-leaf sub-clause compares against, so the two must differ here or that
#: clause would be inactive and the test would prove nothing.
_PICK_INTERCEPT = {'store': 15.0, 'fulfillment': 10.0}
_PICK_PER_ITEM = 0.5

#: Pickers per channel, DELIBERATELY different: the site crews' uid block starts above the
#: LARGER one, which leaves the fulfillment leaf a gap at uids 3-4 that clause B must not
#: mistake for a defect.
_PICKERS = {'store': 5, 'fulfillment': 3}
_PUT_UIDS = (5, 6)          # the site put pool, above max(_PICKERS)
_RECV_UIDS = (7, 8)         # the site receiving crew, above the pool

_BATCHES = (0, 1, 2)

#: `{channel: {sku: handle_var}}` — three SKUs each, partitioned between the channels the
#: way the coordinator partitions the dock's records (by SKU, before the driver stamps
#: anything), so one leaf never holds the other's rows.
_SKUS = {'store': {1: 1.25, 2: 2.5, 3: 0.75},
         'fulfillment': {11: 0.4, 12: 0.9, 13: 1.6}}


def _unload_rows(channel: str):
    """`(batch, sku, qty, duration)` for every receive row of one leaf.

    `duration = C + qty * handle_var` — the identity check 6 re-prices against, written
    forwards here so the fixture cannot agree with the checker by sharing its arithmetic.
    The quantities differ per row, which is what site-dock 17's fixture lacked: with one SKU
    at qty 1 the handle term cancels however the price was computed and check 6 is vacuous.
    """
    out = []
    for b in _BATCHES:
        for i, (sku, hv) in enumerate(sorted(_SKUS[channel].items())):
            qty = b + i + 1
            out.append((b, sku, qty, _C[channel] + qty * hv))
    return out


def _work_event(batch, seq, uid, role, event_type, sku=None, qty=0, duration=0.0):
    """One `work_events` row in `_WORK_EVENT_COLS` order."""
    return (batch, seq, float(seq), float(seq), 0, uid, 0, role, 'foot', event_type,
            None, sku, qty, duration, 'test')


def _leaf_db(tmp_path, channel: str, *, put_uids=_PUT_UIDS, recv_uids=_RECV_UIDS,
             rows=None, extra_events=()) -> str:
    """One channel leaf's sim DB, through the real writers."""
    path = str(tmp_path / f'sim_{channel}.db')
    pdata.init_run_db(path)
    run_id = pdata.create_run(
        path, 'comparison',
        {'pick_intercept': _PICK_INTERCEPT[channel], 'k_pickers': _PICKERS[channel]},
        identity={'channel': channel, 'strategy_key': f'arm_{channel}'})
    pdata.save_sku_scores(path, run_id, [
        (sku, None, _PICK_INTERCEPT[channel] + _PICK_PER_ITEM + hv, hv, 1.0, 1.0, 10, 5, 1.0)
        for sku, hv in sorted(_SKUS[channel].items())])

    events, seq = [], 0
    for b in _BATCHES:
        for uid in range(_PICKERS[channel]):
            events.append(_work_event(b, seq, uid, 'pick', 'pick', duration=3.0)); seq += 1
        for uid in put_uids:
            events.append(_work_event(b, seq, uid, 'put', 'put', duration=4.0)); seq += 1
    for i, (b, sku, qty, dur) in enumerate(_unload_rows(channel) if rows is None else rows):
        events.append(_work_event(b, seq, recv_uids[i % len(recv_uids)], 'receive',
                                  'receive', sku=sku, qty=qty, duration=dur)); seq += 1
    events.extend(extra_events)
    pdata.save_work_events(path, run_id, events)

    # The leaf's OWN per-batch share, so the per-leaf checks 1 and 2 pass on this fixture
    # too. They are what `main` runs first, and a fixture whose leaves were individually
    # red would make every pair verdict below unreadable.
    recv = {}
    for b, _sku, _qty, dur in (_unload_rows(channel) if rows is None else rows):
        n, s = recv.get(b, (0, 0.0))
        recv[b] = (n + 1, s + dur)
    pdata.save_batch_stats(path, run_id, [pdata.BatchStats(
        run_id=run_id, batch_id=b, duration=100.0, num_tasks=1, total_items=1,
        avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5, work_day=b,
        recv_unloaded=recv.get(b, (0, 0.0))[0],
        recv_seconds=recv.get(b, (0, 0.0))[1]) for b in _BATCHES])
    return path


def _site_db(tmp_path, *, totals=None, write_table=True) -> str:
    """The pair's `<pair>/_site/inbound_*.db`, with the dock's own per-batch totals.

    Derived from the two leaves' rows on purpose — a fixture, unlike the production path,
    has no second accumulator — but every test that leans on the closure PLANTS the
    divergence in one side only, which is the same evidence.
    """
    path = str(tmp_path / 'inbound_arm_store__arm_fulfillment.db')
    pdata.init_run_db(path)
    run_id = pdata.create_run(path, 'site', identity={'strategy_key': 'pair'})
    if totals is None:
        totals = []
        for b in _BATCHES:
            secs = sum(d for ch in _C for bb, _s, _q, d in _unload_rows(ch) if bb == b)
            n = sum(1 for ch in _C for bb, _s, _q, _d in _unload_rows(ch) if bb == b)
            totals.append((b, 0, n, 0, secs))
    pdata.save_site_inbound(path, run_id, yard_trailers=[], yard_drains=[(0, 1, 1, 0, 0)],
                            site_receiving=totals)
    if not write_table:
        con = sqlite3.connect(path)
        try:
            con.execute('DROP TABLE site_receiving')
            con.commit()
        finally:
            con.close()
    return path


@pytest.fixture
def pair(tmp_path):
    """A clean coupled pair: two leaf DBs and the site's own dock totals."""
    store = _leaf_db(tmp_path, 'store')
    ful = _leaf_db(tmp_path, 'fulfillment')
    return [store, ful], _site_db(tmp_path)


def _reprice(db_path: str, *, channel_c: float, new_c: float, limit: int | None = None):
    """Move receive rows from one unload constant onto another — the contamination defect.

    Edits DURATIONS only, which is exactly what charging a row at the other channel's rate
    does: the SKU, the quantity and the handle term are untouched, so nothing but the
    re-priced residual can give it away.
    """
    con = sqlite3.connect(db_path)
    try:
        ids = [r[0] for r in con.execute(
            "SELECT rowid FROM work_events WHERE role='receive' ORDER BY rowid")]
        for rid in ids[:limit] if limit else ids:
            con.execute('UPDATE work_events SET duration = duration - ? + ? WHERE rowid=?',
                        (channel_c, new_c, rid))
        con.commit()
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# 0. the clean pair — and the non-vacuity the rest of the file rests on
# ══════════════════════════════════════════════════════════════════════════════

def test_a_clean_coupled_pair_passes_and_the_fixture_is_not_vacuous(pair):
    """Every clause GREEN, and each one proved to have had a subject.

    The assertions on the fixture come FIRST and they are the point: site-dock 17's check-6
    fixture was 7 packs of one SKU at qty 1, where the residual cancels however the price
    was computed, so the check was green and measuring nothing. A pair whose leaves received
    nothing, or whose two channels priced identically, would pass every clause below for the
    same empty reason.
    """
    leaf_dbs, site_db = pair
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert r['verdict'] == 'PASS', r
    assert r['active'], 'the pair recorded nothing; every clause below is vacuous'
    # the evidence, counted before the verdict is trusted
    assert set(r['leaves']) == {'store', 'fulfillment'}
    for ch, lf in r['leaves'].items():
        assert lf['receive_rows'] == len(_BATCHES) * len(_SKUS[ch]) == 9
        assert lf['unload_constant'] == pytest.approx(_C[ch], abs=1e-9)
        assert lf['pick_constant'] == pytest.approx(
            _PICK_INTERCEPT[ch] + _PICK_PER_ITEM, abs=1e-9)
        # reported beside C so a reader can see WHICH branch of the whole-leaf clause they
        # are in: with the intercepts equal the implication C-must-differ is exact, and
        # with them unequal it needs a cancellation no defect produces.
        assert lf['pick_intercept'] == pytest.approx(_PICK_INTERCEPT[ch])
    assert r['site_batches'] == len(_BATCHES)
    assert r['site_receive_seconds'] > 0
    # the two SEPARATIONS, without which clause D's two sub-clauses are inactive
    assert r['unload_constant_separation'] == pytest.approx(_C['store'] - _C['fulfillment'])
    assert r['pick_constant_separation'] == pytest.approx(5.0)
    # and every clause actually RAN
    assert set(r['checks']) == {
        'roles_are_classified', 'no_uid_is_site_and_channel',
        'site_uids_above_both_pick_blocks', 'site_seconds_close', 'site_counts_close',
        'unload_price_is_constant_per_leaf', 'no_row_priced_at_the_other_regime',
        'the_two_regimes_price_differently'}, sorted(r['checks'])


def test_the_smaller_leafs_uid_gap_is_not_a_defect(pair):
    """Site-dock 19 leaves the fulfillment leaf a GAP at uids 3-4, because the site crews
    chain off `max(k_pickers)` over BOTH channels. A clause that checked density rather than
    a floor would fail this healthy pair — which is the trap the retired contiguity check is
    a monument to."""
    leaf_dbs, site_db = pair
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert r['site_uid_floor'] == max(_PICKERS.values())
    assert r['checks']['site_uids_above_both_pick_blocks']
    con = sqlite3.connect(leaf_dbs[1])
    try:
        uids = {u for (u,) in con.execute('SELECT DISTINCT actor_uid FROM work_events')}
    finally:
        con.close()
    assert {3, 4} & uids == set(), 'the fulfillment leaf has no gap; the test is vacuous'


# ══════════════════════════════════════════════════════════════════════════════
# A. the site crews' uids, across the pair
# ══════════════════════════════════════════════════════════════════════════════

def test_a_putter_inside_the_other_leafs_picker_block_fails(tmp_path):
    """SITE-DOCK 04's defect, and nothing in the repo can see it today. A fulfillment leaf
    that built its put crew off its OWN pick cursor puts its putters at uids 3-4 — inside
    store's picker block — so uid 3 is a putter in one DB and a picker in the other."""
    store = _leaf_db(tmp_path, 'store')
    ful = _leaf_db(tmp_path, 'fulfillment', put_uids=(3, 4))
    r = rr.reconcile_pair([store, ful], _site_db(tmp_path))
    assert r['verdict'] == 'FAIL', r
    assert r['checks']['no_uid_is_site_and_channel'] is False
    assert [u for u, _s, _c in r['crossed_uids']] == [3, 4]


def test_a_within_leaf_collision_is_left_to_check_three(tmp_path):
    """Clause A is the CROSS-LEAF claim and only that. A uid that is a putter and a picker
    in the SAME leaf is check 3's disjointness, which runs per leaf and already names it —
    reporting it here too would give one defect two red checks in two tools, and a reader
    chasing a cross-leaf clause would go looking for a second leaf that is not involved."""
    store = _leaf_db(tmp_path, 'store', put_uids=(2, 6))     # uid 2 is also a store picker
    ful = _leaf_db(tmp_path, 'fulfillment', put_uids=(2, 6))
    r = rr.reconcile_pair([store, ful], _site_db(tmp_path))
    assert r['crossed_uids'] == [], r['crossed_uids']
    assert r['checks']['no_uid_is_site_and_channel'] is True
    # and the per-leaf tool DOES see it, which is what makes the silence above safe
    assert rr.reconcile(store)['checks']['uids_disjoint'] is False


def test_site_uids_below_both_pick_blocks_fail(tmp_path):
    """Clause B: a site crew allocated below the floor. Distinct from clause A — here the
    uids do not COLLIDE with a picker's role in the other leaf, they simply sit inside a
    range the pick crews own, which is how a smaller-than-expected block goes unnoticed."""
    store = _leaf_db(tmp_path, 'store', put_uids=(1, 2), recv_uids=(3, 4))
    ful = _leaf_db(tmp_path, 'fulfillment', put_uids=(1, 2), recv_uids=(3, 4))
    r = rr.reconcile_pair([store, ful], _site_db(tmp_path))
    assert r['verdict'] == 'FAIL', r
    assert r['checks']['site_uids_above_both_pick_blocks'] is False
    assert r['site_uids_below_floor'] == [1, 2, 3, 4]


def test_an_unclassified_role_fails_rather_than_being_skipped(tmp_path, pair):
    """The partition of roles into site and per-channel is asserted TOTAL. A fourth role
    added to `Warehouse/operations/roles.py` would otherwise be silently unclassified and
    both uid clauses would pass over every row of it."""
    leaf_dbs, site_db = pair
    con = sqlite3.connect(leaf_dbs[0])
    try:
        con.execute("UPDATE work_events SET role='haul' WHERE role='put'")
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert r['verdict'] == 'FAIL'
    assert r['unclassified_roles'] == ['haul']
    assert r['checks']['roles_are_classified'] is False


# ══════════════════════════════════════════════════════════════════════════════
# C. the site total closes against the two leaves' rows
# ══════════════════════════════════════════════════════════════════════════════

def test_a_lost_receive_row_breaks_the_site_closure(pair):
    """One row dropped from one leaf. The site dock charged it, so the two accumulators come
    apart — and the report names the BATCH, which a run total could not."""
    leaf_dbs, site_db = pair
    con = sqlite3.connect(leaf_dbs[0])
    try:
        con.execute("DELETE FROM work_events WHERE rowid = "
                    "(SELECT MIN(rowid) FROM work_events WHERE role='receive' "
                    " AND batch_id=1)")
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert r['verdict'] == 'FAIL', r
    assert r['checks']['site_seconds_close'] is False
    assert [b for b, _l, _s in r['site_seconds_mismatches']] == [1]


def test_a_whole_batch_missing_from_both_leaves_breaks_only_the_site_closure(pair):
    """THE DEFECT NO PER-LEAF CHECK CAN SEE, and the reason clause C is not a tautology.

    A run-end writer that misses the final flush (memory `run-end-writers-miss-the-final-
    flush`) takes a batch's `work_events` AND its `batch_stats` with it, so check 1 compares
    two equally-short surfaces on each leaf and PASSES on both. The site dock accrued that
    day's labour on its own counter, and only the closure sees the gap."""
    leaf_dbs, site_db = pair
    for db in leaf_dbs:
        con = sqlite3.connect(db)
        try:
            con.execute('DELETE FROM work_events WHERE batch_id = 2')
            con.commit()
        finally:
            con.close()
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert r['verdict'] == 'FAIL', r
    assert [b for b, _l, _s in r['site_seconds_mismatches']] == [2]
    assert [b for b, _l, _s in r['site_count_mismatches']] == [2]


def test_a_leak_offset_by_a_double_count_still_fails_because_the_check_is_per_batch(pair):
    """A run TOTAL would pass this: batch 0 loses exactly what batch 2 gains. The grain is
    what catches it, which is why site-dock 15 put the site total at one row per batch."""
    leaf_dbs, site_db = pair
    con = sqlite3.connect(leaf_dbs[0])
    try:
        moved = con.execute("SELECT duration FROM work_events WHERE role='receive' "
                            "AND batch_id=0 ORDER BY rowid LIMIT 1").fetchone()[0]
        con.execute('UPDATE work_events SET duration = duration - ? WHERE rowid = '
                    "(SELECT MIN(rowid) FROM work_events WHERE role='receive' "
                    ' AND batch_id=0)', (moved,))
        con.execute('UPDATE work_events SET duration = duration + ? WHERE rowid = '
                    "(SELECT MIN(rowid) FROM work_events WHERE role='receive' "
                    ' AND batch_id=2)', (moved,))
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert abs(r['leaf_receive_seconds'] - r['site_receive_seconds']) < 1e-9, \
        'the run total agrees; without the per-batch grain this defect is invisible'
    assert r['verdict'] == 'FAIL'
    assert [b for b, _l, _s in r['site_seconds_mismatches']] == [0, 2]


def test_the_count_closure_is_independent_of_the_seconds_closure(pair):
    """A row retyped `repack` keeps its seconds and stops being an unload. The seconds
    closure must still pass (a repack IS receiving labour and the dock charged it) and the
    count closure must fail (no `unloaded` counter counts a repack)."""
    leaf_dbs, site_db = pair
    con = sqlite3.connect(leaf_dbs[1])
    try:
        con.execute("UPDATE work_events SET event_type='repack' WHERE rowid = "
                    "(SELECT MIN(rowid) FROM work_events WHERE role='receive')")
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair(leaf_dbs, site_db)
    assert r['checks']['site_seconds_close'] is True
    assert r['checks']['site_counts_close'] is False
    assert r['verdict'] == 'FAIL'


def test_a_pair_that_received_with_no_site_db_fails(tmp_path):
    """Site-dock 15 section 5, sharpened: the leaves received, so a site dock ran and its
    rows were parked and never reached an artifact (site-dock 21's failure exactly)."""
    leaf_dbs = [_leaf_db(tmp_path, 'store'), _leaf_db(tmp_path, 'fulfillment')]
    r = rr.reconcile_pair(leaf_dbs, None)
    assert r['verdict'] == 'FAIL', r
    assert r['checks']['site_totals_present'] is False


def test_a_pair_whose_rows_reached_no_artifact_at_all_still_fails(tmp_path):
    """THE COMPLETEST FORM of the parked-rows defect, and a row-only witness passes it.

    A drain that reached NO artifact writes no `work_events` receive rows AND no site DB.
    Only `batch_stats.recv_unloaded` still remembers that receiving happened — so a check
    that asked "are there receive rows?" would read the total loss as the inbound-off pole
    and report PASS on a run that lost an entire crew's work. `reconcile` already folds all
    three surfaces into `active`; the pair pass reads the same definition."""
    leaf_dbs = [_leaf_db(tmp_path, 'store', rows=[]),
                _leaf_db(tmp_path, 'fulfillment', rows=[])]
    con = sqlite3.connect(leaf_dbs[0])
    try:
        con.execute('UPDATE batch_stats SET recv_unloaded = 9 WHERE batch_id = 0')
        con.commit()
    finally:
        con.close()
    # non-vacuity: there really are no receive ROWS, so only the wider witness can fire
    assert rr.reconcile(leaf_dbs[0])['qty_events'] == 0
    r = rr.reconcile_pair(leaf_dbs, None)
    assert r['verdict'] == 'FAIL', r
    assert r['checks']['site_totals_present'] is False


def test_a_site_db_holding_two_runs_is_refused_not_merged(tmp_path):
    """The site table's PK is `(run_id, batch)`, so a file that acquired a second run has two
    rows per batch. Reading row by row keeps whichever SQLite yields last; summing ADDS an
    abandoned run's counters to a live one. Neither is a number, so the pair is NOT CHECKED
    and says why — `find_run` answers every other query in the repo from the OLDEST run,
    which is what makes a second one dangerous rather than merely redundant."""
    leaf_dbs = [_leaf_db(tmp_path, 'store'), _leaf_db(tmp_path, 'fulfillment')]
    site = _site_db(tmp_path)
    con = sqlite3.connect(site)
    try:
        con.execute("INSERT INTO simulation_runs (run_type, created) VALUES ('site', 'x')")
        rid2 = con.execute('SELECT MAX(run_id) FROM simulation_runs').fetchone()[0]
        con.execute('INSERT INTO site_receiving (run_id, batch, recv_depth, recv_unloaded, '
                    'recv_cut, recv_seconds) VALUES (?,?,?,?,?,?)', (rid2, 0, 0, 1, 0, 4.25))
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair(leaf_dbs, site)
    assert 'site_seconds_close' not in r['checks'], r['checks']
    assert 'site_totals_present' not in r['checks'], 'a refusal is not a missing total'
    assert 'holds 2 run(s)' in (r['site_note'] or ''), r['site_note']


def test_a_leaf_db_holding_two_runs_is_refused(tmp_path):
    """The leaf side of the same rule. `reconcile` deliberately spans every run in a file;
    the PAIR claims are run-scoped, so a second run would put an ABANDONED run's rows on one
    side of the closure."""
    a = _leaf_db(tmp_path, 'store')
    con = sqlite3.connect(a)
    try:
        con.execute("INSERT INTO simulation_runs (run_type, created, channel) "
                    "VALUES ('comparison', 'x', 'store')")
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair([a, _leaf_db(tmp_path, 'fulfillment')], _site_db(tmp_path))
    assert r['verdict'] == 'SKIP'
    assert 'holds 2 run(s)' in r['note'], r['note']


def test_a_leaf_whose_pick_constant_is_not_constant_leaves_the_clause_inactive(tmp_path):
    """The whole-leaf clause compares the two leaves' MINIMUM `labor_cost - handle_var`, and
    leans on those minima differing. A leaf where that difference is SKU-dependent cannot
    support the inference at all — so the clause goes INACTIVE and says why, rather than
    quietly comparing two numbers that are not the constants it needs."""
    store = _leaf_db(tmp_path, 'store')
    ful = _leaf_db(tmp_path, 'fulfillment')
    con = sqlite3.connect(ful)
    try:
        con.execute('UPDATE sku_scores SET labor_cost = labor_cost + 3.0 '
                    'WHERE sku = (SELECT MIN(sku) FROM sku_scores)')
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair([store, ful], _site_db(tmp_path))
    assert 'the_two_regimes_price_differently' not in r['checks']
    assert 'INACTIVE' in r['pick_price_note']


def test_an_infinite_duration_is_a_failure_not_a_silent_pass(tmp_path):
    """THE ONE WAY A SECONDS CHECK CAN BE GREEN ABOUT NOTHING, and it is reachable.

    SQLite stores a Python NaN as NULL, so a NaN cannot come back out of these queries — but
    INFINITY round-trips, and `SUM` of a column holding one is `inf`. Then two things break
    at once: `_tol_for(inf)` is `inf`, so `abs(x - inf) > inf` is False for ANY finite other
    side; and if both sides are infinite `abs(inf - inf)` is NaN, which compares False
    against everything. Either way a pure comparison reports agreement.
    """
    leaf_dbs = [_leaf_db(tmp_path, 'store'), _leaf_db(tmp_path, 'fulfillment')]
    con = sqlite3.connect(leaf_dbs[0])
    try:
        con.execute('UPDATE work_events SET duration = 1e400 WHERE rowid = '
                    "(SELECT MIN(rowid) FROM work_events WHERE role='receive')")
        con.commit()
        got = con.execute("SELECT SUM(duration) FROM work_events "
                          "WHERE role='receive'").fetchone()[0]
    finally:
        con.close()
    import math
    assert math.isinf(got), f'the plant did not survive the round trip ({got!r})'
    r = rr.reconcile_pair(leaf_dbs, _site_db(tmp_path))
    assert r['checks']['site_seconds_close'] is False, r
    assert rr.reconcile(leaf_dbs[0])['checks']['seconds_agree'] is False


def test_a_coupled_pair_that_received_nothing_is_a_pass_with_no_site_db(tmp_path):
    """THE INBOUND-OFF POLE, which site-dock 06 runs in EVERY cell. It fields no dock and
    writes no site DB, so 15's unconditional FAIL would have reddened half the campaign."""
    leaf_dbs = [_leaf_db(tmp_path, 'store', rows=[]),
                _leaf_db(tmp_path, 'fulfillment', rows=[])]
    r = rr.reconcile_pair(leaf_dbs, None)
    assert r['verdict'] == 'PASS', r
    assert r['active'] is False
    assert 'site_totals_present' not in r['checks']


def test_a_site_db_without_the_table_is_inactive_and_says_so(tmp_path):
    """A site DB written by site-dock 24's build has no `site_receiving`. The closure cannot
    be evaluated on it, and INACTIVE-with-a-note is the honest report — a green check would
    claim the identity was verified on a file that cannot answer."""
    leaf_dbs = [_leaf_db(tmp_path, 'store'), _leaf_db(tmp_path, 'fulfillment')]
    r = rr.reconcile_pair(leaf_dbs, _site_db(tmp_path, write_table=False))
    assert 'site_seconds_close' not in r['checks']
    assert 'site_totals_present' not in r['checks']
    assert 'predates' in (r['site_note'] or '')


# ══════════════════════════════════════════════════════════════════════════════
# D. the two constants are per regime — check 6's cross-leaf half
# ══════════════════════════════════════════════════════════════════════════════

def test_one_row_charged_at_the_other_regimes_rate_is_named(tmp_path):
    """The second equality. The per-leaf spread goes red too — but only this says WHICH
    rate the row was charged at, which is the difference between "spread 2.5 s" and "1 store
    row was priced at the fulfillment rate"."""
    store = _leaf_db(tmp_path, 'store')
    ful = _leaf_db(tmp_path, 'fulfillment')
    _reprice(store, channel_c=_C['store'], new_c=_C['fulfillment'], limit=1)
    r = rr.reconcile_pair([store, ful], _site_db(tmp_path))
    assert r['verdict'] == 'FAIL', r
    assert r['checks']['no_row_priced_at_the_other_regime'] is False
    assert r['rows_priced_at_the_other_regime'] == {'store': 1}
    assert r['checks']['unload_price_is_constant_per_leaf'] is False


def test_a_whole_leaf_charged_at_the_other_regimes_rate_fails(tmp_path):
    """THE CLAUSE THE PER-LEAF CHECK IS BLIND TO, in both leaves at once.

    Every fulfillment row moved onto the STORE constant: the fulfillment leaf's spread is
    still zero (it is uniformly mispriced), no row prices at "the other" constant because
    the two measured constants now coincide, and check 6 is green on both DBs. What catches
    it is that the two channels' recorded PICK constants still differ — 15.5 s against
    10.5 s — and `C` is a positive linear map of the same two coefficients through
    site-wide scales, so two different pick constants cannot produce one unload price."""
    store = _leaf_db(tmp_path, 'store')
    ful = _leaf_db(tmp_path, 'fulfillment')
    _reprice(ful, channel_c=_C['fulfillment'], new_c=_C['store'])
    r = rr.reconcile_pair([store, ful], _site_db(tmp_path))
    # the per-leaf check really is blind: this is the assertion that makes the test sharp
    assert r['checks']['unload_price_is_constant_per_leaf'] is True
    assert 'no_row_priced_at_the_other_regime' not in r['checks']
    assert r['checks']['the_two_regimes_price_differently'] is False
    assert r['verdict'] == 'FAIL', r


def test_two_channels_on_one_pick_config_leave_the_cross_clauses_inactive(tmp_path,
                                                                          monkeypatch):
    """Two channels MAY run the same pick config. From two sim DBs, "the same config" and
    "one regime resolved to the other" are the same reading — so the clause is INACTIVE and
    SAYS SO, rather than passing silently or failing a legitimate run."""
    monkeypatch.setitem(_C, 'fulfillment', _C['store'])
    monkeypatch.setitem(_PICK_INTERCEPT, 'fulfillment', _PICK_INTERCEPT['store'])
    leaf_dbs = [_leaf_db(tmp_path, 'store'), _leaf_db(tmp_path, 'fulfillment')]
    r = rr.reconcile_pair(leaf_dbs, _site_db(tmp_path))
    assert r['verdict'] == 'PASS', r
    assert 'no_row_priced_at_the_other_regime' not in r['checks']
    assert 'the_two_regimes_price_differently' not in r['checks']
    assert 'INACTIVE' in r['site_price_note']


def test_two_constants_a_hair_apart_do_not_fail_a_clean_pair(tmp_path, monkeypatch):
    """The cross-price clause needs the two constants separated by more than TWICE the
    tolerance, not once. At a separation inside `(_TOL, 2 * _TOL]` every CLEAN row of one
    leaf is also within `_TOL` of the other leaf's constant, so a one-tolerance band would
    report every row of a healthy pair as cross-priced. Unreachable on real data — the two
    constants differ by ~2.5 s or are identical — and pinned so the two branches stay
    mutually exclusive by construction rather than by luck."""
    monkeypatch.setitem(_C, 'fulfillment', _C['store'] - 1.5 * rr._TOL)
    leaf_dbs = [_leaf_db(tmp_path, 'store'), _leaf_db(tmp_path, 'fulfillment')]
    r = rr.reconcile_pair(leaf_dbs, _site_db(tmp_path))
    assert rr._TOL < r['unload_constant_separation'] <= 2 * rr._TOL, r
    assert 'no_row_priced_at_the_other_regime' not in r['checks'], r['checks']
    assert r['verdict'] == 'PASS', r


def test_two_leaves_of_one_channel_are_not_a_pair(tmp_path):
    """A pair is two leaves of DIFFERENT channels, and the channel comes off
    `simulation_runs.channel` rather than off the directory the file sits in — the store
    CONFIG and the store CHANNEL are both named `store`."""
    a = _leaf_db(tmp_path, 'store')
    b = str(tmp_path / 'copy.db')
    with open(a, 'rb') as src, open(b, 'wb') as dst:
        dst.write(src.read())
    r = rr.reconcile_pair([a, b], _site_db(tmp_path))
    assert r['verdict'] == 'SKIP'
    assert 'DIFFERENT channels' in r['note']


def test_a_leaf_file_naming_no_channel_is_refused(tmp_path):
    """A NULL `channel` is a legacy store-only run, which predates channels entirely. The
    channel is what says WHICH leaf each half of the pair is, so a tool that let a NULL
    through would be comparing a leaf it cannot name."""
    a = _leaf_db(tmp_path, 'store')
    con = sqlite3.connect(a)
    try:
        con.execute('UPDATE simulation_runs SET channel = NULL')
        con.commit()
    finally:
        con.close()
    r = rr.reconcile_pair([a, _leaf_db(tmp_path, 'fulfillment')], _site_db(tmp_path))
    assert r['verdict'] == 'SKIP'
    assert 'names no channel' in r['note'], r['note']


# ══════════════════════════════════════════════════════════════════════════════
# the tolerance decision, and the pair grouping
# ══════════════════════════════════════════════════════════════════════════════

def test_the_dominant_constant_bucket_is_tied_to_the_tolerance():
    """`_dominant_constant` buckets residuals by rounding, and the bucket has to BE the
    tolerance. A bucket wider than `_TOL` merges two genuinely distinct rates — the exact
    thing the clause exists to tell apart; narrower, and float noise scatters one rate across
    several buckets and the mode stops being the rate the crew charged. Derived rather than
    written twice, and tied here so the derivation cannot be quietly replaced by a literal."""
    import math
    assert rr._BUCKET_DP == round(-math.log10(rr._TOL))
    assert 10 ** -rr._BUCKET_DP == pytest.approx(rr._TOL)


def test_the_seconds_tolerance_scales_with_the_sum_but_not_past_one_unload():
    """`_TOL` was a flat 1e-6 SECONDS against sums that grow with the row count, and it
    FAILED four archived arms on 1.3e-6 s of float re-association over 4,177,040.9 s.

    Both halves of the decision are pinned, with the archive's own numbers:
      * the re-association noise passes, which is the fix;
      * ONE LOST UNLOAD at that same scale still FAILS, which is the objection the decision
        had to answer — a relative tolerance that hid a real discrepancy on a large arm
        would have broken the check that four-arm red was protecting.
    """
    big = 4_177_040.9
    assert rr._tol_for(big, big) > 1.3e-6, 'the archived false FAIL is still a FAIL'
    assert rr._tol_for(big, big) < 1.0, 'the tolerance swallows a whole unload'
    # the floor still holds for a small run and for a comparison against zero
    assert rr._tol_for(0.0) == rr._TOL
    assert rr._tol_for(100.0) == rr._TOL


def test_a_one_unload_loss_on_a_huge_arm_still_fails_check_one(tmp_path):
    """The tolerance change, exercised through `reconcile` on a real file rather than
    argued from `_tol_for` alone: a leaf whose two surfaces differ by one 7.6 s unload over
    a four-million-second arm is a FAIL, while the same arm with 1.3e-6 s of drift PASSES."""
    def _arm(drift):
        path = str(tmp_path / f'arm_{drift!r}.db')
        pdata.init_run_db(path)
        rid = pdata.create_run(path, 'comparison', {})
        total = 4_177_040.9
        pdata.save_work_events(path, rid, [
            _work_event(0, 0, 7, 'receive', 'receive', sku=1, qty=1, duration=total)])
        pdata.save_batch_stats(path, rid, [pdata.BatchStats(
            run_id=rid, batch_id=0, duration=1.0, num_tasks=1, total_items=1,
            avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5,
            recv_unloaded=1, recv_seconds=total + drift)])
        return rr.reconcile(path)
    assert _arm(1.3e-6)['checks']['seconds_agree'] is True
    assert _arm(7.6)['checks']['seconds_agree'] is False


class _RT:
    """The one thing `_split_arm_pair` asks of a resolver: `arm_pair_halves`, which owns the
    rule. Built over the real `RunTree` method with only a `layout` behind it, so the test
    exercises the CONTRACT's inversion rather than a stand-in for it."""

    def __init__(self, channels):
        self.layout = {'channels': list(channels)} if channels is not None else {}

    arm_pair_halves = RunTree.arm_pair_halves


_ORDER = ['store', 'fulfillment']


@pytest.mark.parametrize('arm_pair, arms, want', [
    ('a__b', {'store': {'a': 'A'}, 'fulfillment': {'b': 'B'}}, ['A', 'B']),
    # the joiner INSIDE an arm name: a first-`__` slice would ask for arm 'uni' and find
    # nothing. Site-dock 24 made the arm pair ONE capture for exactly this reason.
    ('uni__fifo__opt__lpt',
     {'store': {'uni__fifo': 'A'}, 'fulfillment': {'opt__lpt': 'B'}}, ['A', 'B']),
    # the same rule on both channels, which the funnel's reference pair really is
    ('fifo__fifo', {'store': {'fifo': 'A'}, 'fulfillment': {'fifo': 'B'}}, ['A', 'B']),
    # THE CAMPAIGN CASE, and the defect a mutation found: both channels draw restock rules
    # from ONE vocabulary, so each leaf holds arms named like BOTH halves. Deciding WHICH
    # channel by membership resolves nothing here and the whole pass reports no pairs;
    # deciding it POSITIONALLY against the declared order is exact.
    ('a__b', {'store': {'a': 'A', 'b': 'Awrong'},
              'fulfillment': {'a': 'Bwrong', 'b': 'B'}}, ['A', 'B']),
    # neither half is on disk: reported, never guessed at
    ('a__b', {'store': {'x': 'A'}, 'fulfillment': {'y': 'B'}}, []),
    # one channel ran its half and the other did not
    ('a__b', {'store': {'a': 'A'}, 'fulfillment': {'y': 'B'}}, []),
])
def test_the_arm_pair_is_resolved_positionally_and_checked_against_disk(arm_pair, arms,
                                                                       want):
    assert rr._split_arm_pair(_RT(_ORDER), arm_pair, arms) == want


def test_an_arm_pair_with_no_declared_channel_order_resolves_nothing():
    """`write_run_layout` writes `channels` on every run, so this is unreachable in
    production — and it REFUSES rather than falling back for that reason: a fallback has to
    guess which half is which channel, and a wrong guess reads one leaf's rows under the
    other channel's name with nothing to say so.

    THE FIXTURE IS THE SWAPPED CASE deliberately: `store` holds the SECOND half and
    `fulfillment` the first, so a fallback that guessed ALPHABETICALLY ('fulfillment' <
    'store') would resolve happily and hand each leaf the other channel's arm. With a
    fixture where the guess fails anyway, this test would pass against the fallback and
    prove nothing.
    """
    arms = {'store': {'b': 'S'}, 'fulfillment': {'a': 'F'}}
    assert rr._split_arm_pair(_RT([]), 'a__b', arms) == []
    assert rr._split_arm_pair(_RT(None), 'a__b', arms) == []
    # ... and the same arms DO resolve once an order is declared that says so
    assert rr._split_arm_pair(_RT(['fulfillment', 'store']), 'a__b', arms) == ['F', 'S']


def test_an_uncoupled_run_yields_no_pairs():
    """`0 coupled pair(s)` rather than a green verdict about a run that has no site. Every
    published run is uncoupled, so this is the common case."""
    class _RT:
        layout = {'coupled': False}
    assert rr._coupled_pairs(_RT()) == ([], [])


# ══════════════════════════════════════════════════════════════════════════════
# main() over a real run tree — the CLI, not the function
# ══════════════════════════════════════════════════════════════════════════════

def _run_tree(tmp_path, *, coupled=True, with_site=True) -> str:
    """A run root in the DECLARED shape: `<cell>/<pair>/<config>/<channel>/sim_<arm>.db`
    plus `<cell>/<pair>/_site/inbound_<arm-pair>.db`.

    Built through the contract's own templates rather than by joining strings, which is the
    trap CLAUDE.md section 3 names: the store CONFIG and the store CHANNEL are both `store`,
    so `<pair>/store/store/` is a real path and a walker that matched directory NAMES would
    resolve the wrong level.
    """
    import json
    from Optimization.runschema import contract as _contract

    root = tmp_path / 'run'
    for ch in ('store', 'fulfillment'):
        leaf_dir = root / 'cellA' / 'pairA' / ch / ch
        leaf_dir.mkdir(parents=True, exist_ok=True)
        src = _leaf_db(tmp_path, ch)
        (leaf_dir / f'sim_arm_{ch}.db').write_bytes(open(src, 'rb').read())
    if with_site:
        site_dir = root / 'cellA' / 'pairA' / '_site'
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / 'inbound_arm_store__arm_fulfillment.db').write_bytes(
            open(_site_db(tmp_path), 'rb').read())
    # `cells` is a list of DICTS carrying a `name`, and `runlayout.cells` walks them in
    # DESCRIPTOR ORDER with no disk probe -- a list of bare strings makes the whole walk
    # yield nothing, silently, which is how this fixture first reported `0 coupled pair(s)`
    # over a tree that had one.
    (root / 'run_layout.json').write_text(json.dumps(
        {'schema_id': _contract.head(), 'cells': [{'name': 'cellA'}],
         'channels': ['store', 'fulfillment'], 'coupled': coupled}),
        encoding='utf-8')
    return str(root)


def test_main_reports_the_coupled_pair_and_exits_zero(tmp_path, capsys):
    """THE CLI, driven end to end over a real tree — not `reconcile_pair` in isolation.

    Site-dock 24's worst defect was invisible to its own review and to 110 tests, and only
    showed when the stage was driven on a real tree: the grouping resolved nothing and the
    stage logged the same line a run with no work logs. So the grouping is exercised here
    through `main`, and the assertion is on what it PRINTED.
    """
    rc = rr.main([_run_tree(tmp_path), '--verbose'])
    printed = capsys.readouterr().out
    assert ('1 coupled pair(s) with a site dock, 0 arm(s) or pair(s) without one, '
            '0 FAILED') in printed, printed
    assert 'cellA/pairA/arm_store__arm_fulfillment' in printed
    # `pick_intercept` beside C and pick_C: the whole-leaf clause's own comment promises a
    # reader can tell which branch of its implication they are in, and this line is the only
    # place they could. A value computed and never shown is that promise unkept.
    assert 'pick_intercept=15.000000s' in printed, printed
    assert rc == 0


def test_main_prints_a_pair_it_could_not_check(tmp_path, capsys):
    """A pair that was NOT CHECKED is neither a pass nor a failure, and the whole reason this
    pass exists is that a tool which says nothing about a scope reads as one that checked it.
    SKIP pairs print in both modes and are counted separately in the summary."""
    root = _run_tree(tmp_path)
    leaf = glob.glob(os.path.join(root, 'cellA', 'pairA', 'store', 'store', '*.db'))[0]
    con = sqlite3.connect(leaf)
    try:
        con.execute("INSERT INTO simulation_runs (run_type, created, channel) "
                    "VALUES ('comparison', 'x', 'fulfillment')")
        con.commit()
    finally:
        con.close()
    rc = rr.main([root])
    printed = capsys.readouterr().out
    assert 'SKIP cellA/pairA/arm_store__arm_fulfillment' in printed, printed
    assert '1 NOT CHECKED' in printed, printed
    assert rc == 0, 'a pair that was not checked is not a failure'


def test_main_fails_when_a_pair_that_received_has_no_site_db(tmp_path, capsys):
    """The leaves received and no site DB exists: the site-scoped rows were parked and never
    reached an artifact. Reachable, unlike site-dock 15's unconditional form."""
    rc = rr.main([_run_tree(tmp_path, with_site=False)])
    printed = capsys.readouterr().out
    assert 'NO site DB' in printed, printed
    assert rc == 1


def test_main_fails_one_arm_pair_whose_site_db_is_missing(tmp_path, capsys):
    """THE SHAPE 15 SECTION 5 WAS WRITTEN FOR, and reading the pairing off the filenames is
    what nearly made it unreachable.

    On a 34-arm coupled cell one arm pair's rows are parked and its `inbound_*.db` never
    reaches disk. The other 33 files still name 33 pairs, so an enumeration that reads the
    pairing off the stems finds 33 healthy pairs, reports them green and exits 0 — the
    missing one is not FAIL, not MISSING, just absent. The arms each leaf RAN are what say
    otherwise, and they are on disk either way.

    Built with TWO arm pairs so the survivor is there to be reported green beside the
    casualty; with one, deleting the only site DB is the whole-pair case already covered.
    """
    root = _run_tree(tmp_path)
    # a second arm in each leaf, and a second site DB naming them as a pair
    for ch in ('store', 'fulfillment'):
        leaf_dir = os.path.join(root, 'cellA', 'pairA', ch, ch)
        src = glob.glob(os.path.join(leaf_dir, 'sim_arm_*.db'))[0]
        with open(src, 'rb') as f, open(os.path.join(leaf_dir, f'sim_two_{ch}.db'),
                                        'wb') as d:
            d.write(f.read())
    gone = os.path.join(root, 'cellA', 'pairA', '_site',
                        'inbound_two_store__two_fulfillment.db')
    assert not os.path.exists(gone), 'the second pair site DB must be the MISSING one'

    rc = rr.main([root])
    printed = capsys.readouterr().out
    assert 'cellA/pairA/store:two_store' in printed, printed
    assert 'NO site DB names them' in printed, printed
    assert rc == 1
    # ... and the pair that DID write one is still reported as healthy beside it
    assert '1 coupled pair(s) with a site dock' in printed, printed


def test_main_fails_a_dockless_pair_whose_rows_reached_no_artifact(tmp_path, capsys):
    """The same total-loss case at the CLI, where the dockless rule actually lives. No site
    DB, no receive ROWS on either leaf, and only `batch_stats.recv_unloaded` still
    remembering that a crew worked — a row-only witness reads that as the inbound-off pole
    and exits 0 over a run that lost an entire crew's output."""
    root = _run_tree(tmp_path, with_site=False)
    for db in glob.glob(os.path.join(root, 'cellA', 'pairA', '*', '*', '*.db')):
        con = sqlite3.connect(db)
        try:
            con.execute("DELETE FROM work_events WHERE role='receive'")
            con.commit()
        finally:
            con.close()
    rc = rr.main([root])
    printed = capsys.readouterr().out
    assert 'NO site DB' in printed, printed
    assert rc == 1


def test_main_on_an_uncoupled_tree_says_so_rather_than_staying_silent(tmp_path, capsys):
    """The tool lying by omission across the entire archive is the failure this line exists
    to prevent: every published run is uncoupled."""
    rc = rr.main([_run_tree(tmp_path, coupled=False)])
    printed = capsys.readouterr().out
    assert '0 coupled pair(s)' in printed, printed
    assert rc == 0
