"""sim_semantics — the sim_db (and keyframes) column semantics, declared beside their DDL.

The structured form of every warning `Picking_Data.py`'s DDL comments carry in prose: kind,
unit, unit of account, grain, clock, null-meaning and the scar that earned each tag.  Keyed
exactly as `SIM_DB_FAMILY.declared_shape()['tables']` keys its columns, so the completeness
gate (`Tests/architecture/test_column_semantics.py`) is a dict diff with nothing to drift.

Every table of both families is COVERED — the convention pass left no remainder.

Tags are metadata: importing this module changes no DDL, no shape id, no result.
"""
from __future__ import annotations

from Schema.semantics import (
    BATCHES, ByDiscriminator, Col, COUNT, FLOW, LABEL, LEVEL, PACKS, PIECES, RATE, SCORE,
    SHARE, SIM, SPAN, STAMP, register)

_KEY = Col(LABEL, 'id', 'row')
_BAY = Col(LABEL, 'coordinate', 'row')
_ENUM = Col(LABEL, 'enum', 'row')

SIM_DB_SEMANTICS: dict = {
    'batch_stats': {
        'id':                 _KEY,
        'run_id':             _KEY,
        'batch_id':           _KEY,
        'duration':           Col(SPAN, 's', 'batch', clock=SIM,
                                  note='the batch MAKESPAN (first start -> last finish), '
                                       'never the elapsed day — thr_batch divides by it'),
        'num_tasks':          Col(COUNT, 'tasks', 'batch'),
        'total_items':        Col(COUNT, 'items', 'batch', account=PIECES,
                                  note='PICKED; items_demanded >= total_items is the check '
                                       'that caught the per-bin over-pick'),
        'task_makespan':      Col(SPAN, 's', 'batch', clock=SIM,
                                  note='sum of task durations = total labour; invariant '
                                       '== SUM(task_stats.duration)'),
        'thr_task':           Col(RATE, 'items/s', 'batch', clock=SIM, per='labour'),
        'thr_batch':          Col(RATE, 'items/s', 'batch', clock=SIM, per='makespan',
                                  note='the rate the crew worked AT, not the rate the day '
                                       'delivered — that is thr_elapsed, and a scheduling '
                                       'change moves the two in opposite directions (4x '
                                       'apart at a 400 s slot with a 100 s wave)'),
        'avg_concurrent_pickers': Col(LEVEL, 'pickers', 'batch',
                                  note='time-averaged level; its divisor was anchored at a '
                                       'literal 0.0 before the stamp/span fix'),
        'picking_pct':        Col(SHARE, '1', 'batch',
                                  note='0-1 in the DB, x100 at the frame boundary; sums to '
                                       '1 with traveling_pct for ONE crew only'),
        'traveling_pct':      Col(SHARE, '1', 'batch'),
        'batch_start_time':   Col(STAMP, 's', 'arm', clock=SIM,
                                  note='per-ARM axis: two arms’ stamps are not comparable'),
        'batch_end_time':     Col(STAMP, 's', 'arm', clock=SIM),
        'sigma_fd':           Col(SCORE, 's-weighted', 'batch'),
        'pick_owed_s':        Col(SCORE, 's', 'batch',
                                  note='pick seconds the PLANNED demand owes this '
                                       'placement; read with unservable_weight, never '
                                       'alone -- an absent SKU costs it nothing'),
        'unservable_weight':  Col(SCORE, 'lines', 'batch',
                                  note='planned demand pick_owed_s declined to price '
                                       '(no shelf stock); non-zero means the score was '
                                       'decided by availability, not placement'),
        'pick_owed_exact_s':  Col(SCORE, 's', 'batch',
                                  null_means='this batch took no check -- it is not a '
                                             'keyframe batch, or the run predates the '
                                             'column, or it carried no derived staffing '
                                             'block for the expectation to read. NOT a '
                                             'placement that owes nothing, which is the '
                                             'best score the column can take',
                                  note='the closed form re-taken over this placement, on '
                                       'KEYFRAME BATCHES ONLY (NULL elsewhere) and in '
                                       'seconds PER UNIT -- a different model at a '
                                       'different grain, for rank agreement, never to be '
                                       'compared with pick_owed_s as a value'),
        'reload_moves':       Col(COUNT, 'moves', 'batch'),
        'reorder_placements': Col(COUNT, 'units', 'batch', account=PACKS,
                                  note='storage units placed, not merchandise'),
        'skus_reordered':     Col(COUNT, 'skus', 'batch'),
        'units_ordered':      Col(COUNT, 'units', 'batch', account=PIECES,
                                  note='merchandise ordered — packs do not exist yet at '
                                       'order time'),
        'items_demanded':     Col(COUNT, 'items', 'batch', account=PIECES),
        'queue_depth':        Col(LEVEL, 'units', 'batch', account=PACKS,
                                  note='disjoint from recv_depth; a reader wanting the '
                                       'whole unbinned backlog sums the two — same account'),
        'lead_queue_depth':   Col(LEVEL, 'orders', 'batch',
                                  note='in-flight reorder ENTRIES, not packs and not '
                                       'pieces — in_transit_qty carries the merchandise'),
        'in_transit_qty':     Col(LEVEL, 'units', 'batch', account=PIECES,
                                  note='the OTHER unit of account: 2.00x from the pack '
                                       'depths on a measured batch, never added to them'),
        'work_day':           Col(LABEL, 'day', 'batch', logical='release_day',
                                  note='the RELEASE day, not the shift work landed in — '
                                       'work_events.shift_index measurably comes apart '
                                       'from it'),
        'released_late':      Col(SPAN, 's', 'batch', clock=SIM,
                                  note='the only record of a miss the release clamp erases'),
        'recv_depth':         Col(LEVEL, 'units', 'batch', account=PACKS),
        'recv_unloaded':      Col(FLOW, 'units', 'batch', account=PACKS),
        'recv_cut':           Col(LEVEL, 'units', 'batch', account=PACKS,
                                  note='re-counts the whole standing dock every batch; '
                                       'SUM(recv_cut) published a 101x wrong headline — '
                                       'the additive statistic is count of batches '
                                       'non-zero'),
        'recv_seconds':       Col(FLOW, 's', 'batch', clock=SIM,
                                  note='receiving labour; deliberately in NO put-away '
                                       'total — that figure has been published'),
        # ── put-away rework (ADR-0003) ──────────────────────────────────────────────
        'put_topups':         Col(FLOW, 'units', 'batch', account=PACKS,
                                  note='top-ups into a bin already holding the SKU; '
                                       'non-zero only when the free index was dry, so it '
                                       'is read against free_bins and never alone'),
        'put_spills':         Col(FLOW, 'units', 'batch', account=PACKS,
                                  null_means='a vintage before 2026-09-10 never counted '
                                             'the spill; unknown, never zero',
                                  note='placements into a LARGER size tier than the '
                                       'unit\'s own -- the chain spilled up because the '
                                       'unit\'s bucket was dry; the first deviation from '
                                       'the put pricer\'s per-class assumption and judged '
                                       'at exactly zero (no knob); singletons never spill'),
        'recv_repacks':       Col(FLOW, 'units', 'batch', account=PACKS,
                                  note='rescue ACTS (repack or singleton); the staffing '
                                       'record expects zero and the equilibrium audit '
                                       'flags any run where it is not'),
        'recv_repacked_packs': Col(FLOW, 'units', 'batch', account=PACKS,
                                   note='packs those acts produced -- the labour is priced '
                                        'per PACK and lands in recv_seconds; the ratio to '
                                        'recv_repacks says how far a unit was split'),
        'free_bins':          Col(LEVEL, 'bins', 'batch',
                                  note='free index at batch end; re-counts every still-free '
                                       'bin every batch, so SUM is meaningless -- same trap '
                                       'as recv_cut.  THE WHOLE GEOMETRY, not the leaf\'s '
                                       'section: a channel leaf counts the other channel\'s '
                                       'untouched bins as free (57-59% read where the '
                                       'section\'s headroom was 15-18%, memory '
                                       'free-bins-counts-the-whole-geometry); the '
                                       'per-bucket reading is the free_index table'),
        'is_outlier':         Col(LABEL, 'flag', 'batch'),
    },
    # ── the free index per bucket (department-calibration 32, decision 3) ───────────
    'free_index': {
        'run_id':   _KEY,
        'batch_id': _KEY,
        'handling': _ENUM,      # the BinKey's four coordinates, the record's bucket key
        'category': _ENUM,
        'size':     _ENUM,
        'unit':     _ENUM,      # 'pallet' | 'singleton' | 'fulfillment': the regime, so a
                                # leaf's own section is the rows in its regime
        'free':     Col(LEVEL, 'bins', 'batch',
                        note='this bucket\'s free index, read at the same instant as '
                             'batch_stats.free_bins; re-measured per batch so SUM across '
                             'batches is meaningless -- read per bucket over a window '
                             '(minimum, mean, drawdown from the record\'s stamped setup '
                             'free); ZERO is a real reading (a dry bucket), not a gap'),
    },
    'carryover': {
        'run_id':   _KEY,
        'batch_id': _KEY,
        'reason':   Col(LABEL, 'enum', 'row'),
        'sku':      _KEY,
        'qty': ByDiscriminator('reason', {
            'dock':                  Col(LEVEL, 'units', 'batch', account=PACKS),
            'unplaced':              Col(LEVEL, 'units', 'batch', account=PACKS),
            'held':                  Col(LEVEL, 'units', 'batch', account=PACKS),
            'unpicked_daycut':       Col(FLOW, 'items', 'batch', account=PIECES),
            'unpicked_unavailable':  Col(FLOW, 'items', 'batch', account=PIECES),
            'unpicked_unstocked':    Col(FLOW, 'items', 'batch', account=PIECES),
            'unpicked_notasks':      Col(FLOW, 'items', 'batch', account=PIECES),
        }, note='three reason families under one PK: put-side LEVELS re-emitted every '
                'batch vs pick-side FLOWS; 500 units once vanished when two producers '
                'shared a reason, and the table now raises instead of OR REPLACE'),
    },
    'put_queue_state': {
        'run_id':     _KEY,
        'batch_id':   _KEY,
        'queue':      _ENUM,
        'depth':      Col(LEVEL, 'units', 'batch', account=PACKS),
        'oldest_age': Col(SPAN, 'batches', 'batch', clock=BATCHES,
                          null_means='the queue is empty',
                          note='elapsed batches since the head item arrived'),
        'staging':    Col(LEVEL, 'units', 'batch', account=PACKS,
                          null_means='an unbounded floor — a meaningfully different '
                                     'configuration from any finite limit'),
        'admitted':   Col(FLOW, 'units', 'batch', account=PACKS),
        'placed':     Col(FLOW, 'units', 'batch', account=PACKS),
        'blocked':    Col(FLOW, 'units', 'batch', account=PACKS),
        'cart_swaps': Col(COUNT, 'swaps', 'batch'),
        'cut':        Col(LEVEL, 'units', 'batch', account=PACKS,
                          note='the canonical level-mislabelled-flow: resets like its '
                               'three flow neighbours but re-counts the standing queue '
                               '(101x when summed); count non-zero batches instead'),
    },
    'work_events': {
        'id':          _KEY,
        'run_id':      _KEY,
        'batch_id':    _KEY,
        'seq':         Col(LABEL, 'ordinal', 'row',
                           note='the declared total order within a batch'),
        't_abs':       Col(STAMP, 's', 'arm', clock=SIM,
                           note='per-ARM axis; two arms’ t_abs are not comparable, and a '
                                'put row may legitimately carry a smaller t_abs than the '
                                'receive row of the same unit'),
        't_local':     Col(STAMP, 's', 'batch', clock=SIM),
        'shift_index': Col(LABEL, 'frame', 'row', logical='frame_index',
                           note='a reporting frame that labels and never schedules — '
                                'distinct from the shift that dispatches (drain-or-cap)'),
        'actor_uid':   Col(LABEL, 'uid', 'row', space='crew-unique',
                           note='a uid in a local slot lands inside [0,k) and is accepted '
                                '— the id-space confusion _group_events_by_picker now '
                                'raises on'),
        'actor_local': Col(LABEL, 'local-id', 'row', space='crew-local',
                           note='dense PER CREW [0,k); the other id space'),
        'role':        _ENUM,
        'mode':        _ENUM,
        'event_type':  Col(LABEL, 'enum', 'row',
                           note='for put/receive it equals role, with ONE declared exception '
                                '— a repack carries role=receive and event_type=repack, so '
                                'that rework is summable by role and filterable by type '
                                '(metrics.work_events.repack_rows); for pick a row is a state '
                                'change and the work is the span BETWEEN rows'),
        'aisle_id':    Col(LABEL, 'id', 'row',
                           null_means='the event has no aisle (a state change or a '
                                      'non-travel action)'),
        'sku':         Col(LABEL, 'id', 'row',
                           null_means='the event moved no particular SKU'),
        'qty':         Col(FLOW, 'items', 'row', account=PIECES,
                           null_means='the row moved no merchandise',
                           note='SIGNED: pick < 0, put/receive > 0'),
        'duration':    Col(SPAN, 's', 'row', clock=SIM,
                           null_means='the row is an instant, not an interval — a pick '
                                      'event; 29,657 rows once claimed zero seconds '
                                      'because this defaulted to 0'),
        'source':      Col(LABEL, 'enum', 'row',
                           null_means='the event is not an admission'),
    },
    'aisle_metrics': {
        'run_id':        _KEY,
        'batch_id':      _KEY,
        'aisle_id':      _KEY,
        'n_skus':        Col(LEVEL, 'skus', 'batch'),
        'n_bins':        Col(LEVEL, 'bins', 'batch'),
        'demand_sum':    Col(FLOW, 'items', 'batch', account=PIECES,
                             note='this batch’s demand routed to the aisle'),
    },
    'bin_eviction': {
        'run_id':   _KEY,
        'batch_id': _KEY,
        'seq':      Col(LABEL, 'ordinal', 'row',
                        note='run-scoped monotonic, ascending within a batch'),
        'aisle_id': _KEY,
        'bayX':     _BAY,
        'bayY':     _BAY,
        'sku':      _KEY,
        'qty':      Col(FLOW, 'items', 'row', account=PIECES,
                        note='units removed; the unit re-enters the stock queue'),
    },
    'bin_placement': {
        'run_id':     _KEY,
        'batch_id':   _KEY,
        'seq':        Col(LABEL, 'ordinal', 'row',
                          note='RUN-scoped, not per batch — the PK collision note is in '
                               'the DDL'),
        'aisle_id':   _KEY,
        'bayX':       _BAY,
        'bayY':       _BAY,
        'sku':        _KEY,
        'qty':        Col(FLOW, 'items', 'row', account=PIECES,
                          note='units placed into this bin'),
        'cause':      _ENUM,
        'bin_state':  _ENUM,   # 'empty' | 'occupied' (ADR-0003): the bin's state at landing,
                               # independent of `cause`, which is where the unit came FROM
        'unit_size':  Col(LABEL, 'enum', 'row',
                          null_means='a pre-2026-09-10 row, or a top-up row (the rung does '
                                     'not carry the unit it was cut from; a top-up cannot '
                                     'spill)',
                          note='the unit\'s own size tier; differs from bin_size exactly '
                               'when the chain spilled up (batch_stats.put_spills counts '
                               'those rows per batch)'),
        'bin_size':   Col(LABEL, 'enum', 'row',
                          null_means='a pre-2026-09-10 row: the tier was not recorded',
                          note='the landing bin\'s size tier'),
        'score':      Col(SCORE, 'policy-relative', 'row',
                          null_means='no score was recorded — a zero would claim a '
                                     'perfect placement',
                          note='NOT comparable across arms; `policy` says whose scale'),
        'score_rank': Col(LABEL, 'ordinal', 'row',
                          null_means='no ranking group was recorded'),
        'policy':     Col(LABEL, 'enum', 'row',
                          null_means='pre-policy vintage'),
    },
    'bin_scores': {
        'run_id':       _KEY,
        'aisle_id':     _KEY,
        'bayX':         _BAY,
        'bayY':         _BAY,
        'travel_d':     Col(SPAN, 's', 'bin', clock=SIM,
                            note='geometry-fixed travel seconds to this bin'),
        'height_mult':  Col(SCORE, 'multiplier', 'bin'),
        'layout_score': Col(SCORE, 's', 'bin', clock=SIM),
        'map_pref':     Col(SCORE, 'policy-relative', 'bin',
                            null_means='not an optimal-map arm'),
    },
    'picker_events': {
        'id':               _KEY,
        'run_id':           _KEY,
        'batch_id':         _KEY,
        'picker_id':        Col(LABEL, 'local-id', 'row', space='crew-local',
                                note='dense per crew — the pick-only, batch-relative '
                                     'stream'),
        'time':             Col(STAMP, 's', 'batch', clock=SIM),
        'event_type':       _ENUM,
        'aisle_id':         Col(LABEL, 'id', 'row',
                                null_means='a state-change row with no aisle'),
        'bayX':             Col(LABEL, 'coordinate', 'row',
                                null_means='a state-change row with no bin'),
        'bayY':             Col(LABEL, 'coordinate', 'row',
                                null_means='a state-change row with no bin'),
        'sku':              Col(LABEL, 'id', 'row',
                                null_means='a state-change row with no SKU'),
        'quantity':         Col(FLOW, 'items', 'row', account=PIECES,
                                null_means='the row moved no merchandise'),
        'bins_completed':   Col(LEVEL, 'bins', 'session-cumulative',
                                note='SESSION-CUMULATIVE: a realized quantity is the '
                                     'DIFFERENCE between two events; reading one row as a '
                                     'per-task flow repeats the plan-read-as-actuals '
                                     'incident'),
        'total_bins':       Col(LEVEL, 'bins', 'session-cumulative'),
        'items_picked':     Col(LEVEL, 'items', 'session-cumulative', account=PIECES),
        'total_items':      Col(LEVEL, 'items', 'session-cumulative', account=PIECES),
        'pick_travel_x':    Col(SPAN, 's', 'row', clock=SIM,
                                note='accrued since the previous event; the '
                                     'reconciliation identity has FIVE terms plus '
                                     'handling (no column) — not three'),
        'pick_travel_y':    Col(SPAN, 's', 'row', clock=SIM),
        'non_pick_travel_x': Col(SPAN, 's', 'row', clock=SIM),
        'non_pick_travel_y': Col(SPAN, 's', 'row', clock=SIM),
        'cart_move':        Col(SPAN, 's', 'row', clock=SIM),
    },
    'picks': {
        'id':        _KEY,
        'run_id':    _KEY,
        'batch_id':  _KEY,
        'picker_id': Col(LABEL, 'local-id', 'row', space='crew-local'),
        'sim_time':  Col(STAMP, 's', 'batch', clock=SIM),
        'aisle_id':  _KEY,
        'bayX':      _BAY,
        'bayY':      _BAY,
        'sku':       _KEY,
        'quantity':  Col(FLOW, 'items', 'row', account=PIECES),
    },
    'reorder_queue': {
        'run_id':         _KEY,
        'batch_id':       _KEY,
        'kind':           Col(LABEL, 'enum', 'row',
                              note="'lead' in transit | 'stock' awaiting bin | 'held' "
                                   "refused floor | 'dock' awaiting unload"),
        'sku':            _KEY,
        'qty':            Col(LEVEL, 'items', 'batch', account=PIECES,
                              note='a LISTING re-emitted per batch — the contents of the '
                                   'queues, never summed across batches'),
        'remaining_lead': Col(SPAN, 'batches', 'batch', clock=BATCHES,
                              note="batches until LOADING ('lead' rows only): flag-off the "
                                   "whole countdown; flag-on the SUPPLIER lead an order waits "
                                   "at the ordering site before the trailer loads it, then 1 "
                                   "while riding the trailer's absolute-clock lead, 0 standing"),
        'unit_type':      Col(LABEL, 'enum', 'row',
                              null_means="a 'lead' row — still in transit, not yet packed"),
        'storage_size':   Col(LABEL, 'enum', 'row',
                              null_means="a 'lead' row — still in transit, not yet packed"),
        'queue':          Col(LABEL, 'enum', 'row',
                              null_means="a 'lead' row — not yet routed to a put-away "
                                         "queue"),
    },
    'simulation_runs': {
        'run_id':                _KEY,
        'run_type':              _ENUM,
        'created':               Col(LABEL, 'timestamp', 'run',
                                     note='wall-clock TEXT, identity only — never '
                                          'arithmetic'),
        'strategy_key':          Col(LABEL, 'enum', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'pair_label':            Col(LABEL, 'enum', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'config_label':          Col(LABEL, 'enum', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'warehouse_fingerprint': Col(LABEL, 'digest', 'run',
                                     null_means='pre-fingerprint vintage'),
        'inventory_label':       Col(LABEL, 'enum', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'channel':               Col(LABEL, 'enum', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'sim_schema_id':         Col(LABEL, 'digest', 'run',
                                     null_means='pre-stamp vintage'),
        'num_pickers':           Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'x_speed':               Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type',
                                     note='a pace, ft/s upstream of sec_per_inch'),
        'y_speed':               Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'pick_intercept':        Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type',
                                     note='15 s — warehouse-sized seconds; these constants '
                                          'being seconds is what convicted the ms divisor'),
        'pick_weight_coef':      Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'pick_volume_coef':      Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'cart_swap_coef':        Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'k_pickers':             Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'n_batches':             Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'seed_world':            Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'keyframe_interval':     Col(LABEL, 'param', 'run',
                                     null_means='not recorded by this vintage or run type'),
        'optimal_sigma_fd':      Col(LEVEL, 's-weighted', 'run',
                                     null_means='no yardstick computed for this run'),
        'optimal_work':          Col(LEVEL, 's-weighted', 'run',
                                     null_means='no yardstick computed for this run'),
    },
    'sku_scores': {
        'run_id':              _KEY,
        'sku':                 _KEY,
        'map_target':          Col(SCORE, 'policy-relative', 'sku',
                                   null_means='not an optimal-map arm'),
        'labor_cost':          Col(SCORE, 's/unit', 'sku',
                                   null_means='not recorded by this vintage'),
        'handle_var':          Col(SCORE, 's/unit', 'sku',
                                   null_means='not recorded by this vintage'),
        'expected_popularity': Col(RATE, 'freq*items/batch', 'sku', per='batch',
                                   null_means='not recorded by this vintage'),
        'expected_labor':      Col(RATE, 's/batch', 'sku', clock=SIM, per='batch',
                                   null_means='not recorded by this vintage'),
        'equilibrium_qty':     Col(COUNT, 'items', 'sku', account=PIECES,
                                   null_means='not recorded by this vintage',
                                   note='the RUN’s declaration as this arm fielded it, read '
                                        'off the planned inventory at batch 0 — since '
                                        'ADR-0002 a generated catalogue carries no level at '
                                        'all and the declaration lives in '
                                        'inventory_db.stock_levels. NOT comparable ACROSS '
                                        'that break: a pre-ADR-0002 row records a level the '
                                        'planner grew from the catalogue’s authored coverage '
                                        'in BATCHES, which was no span of time'),
        'reorder_point':       Col(COUNT, 'items', 'sku', account=PIECES,
                                   null_means='not recorded by this vintage',
                                   note='declared with equilibrium_qty by the same setup '
                                        'derivation (a coverage in DAYS); under the line '
                                        'floor it encodes a LINE, not lead-time demand'),
        'lead_time_mean':      Col(SPAN, 'batches', 'sku', clock=BATCHES,
                                   null_means='not recorded by this vintage',
                                   note='the batch-denominated lead the trailer feature '
                                        'replaces flag-on; unlike the two columns above it '
                                        'this one IS the SKU’s own fact, straight off '
                                        'cartons, because ADR-0002 retired the stock levels '
                                        'and not the supply side'),
    },
    'yard_trailers': {
        'run_id':    _KEY,
        'seq':       Col(LABEL, 'id', 'row',
                         note='the dispatch ordinal — the SAME trailer in every arm of a '
                              'run, because leads are seq-keyed draws'),
        'arrived_s': Col(STAMP, 's', 'arm', clock=SIM,
                         note='per-ARM axis, like batch_start_time: two arms’ stamps are '
                              'not comparable, and the SPAN between two of these columns '
                              'is the only thing that is'),
        'staged_s':  Col(STAMP, 's', 'arm', clock=SIM,
                         null_means='the trailer was NEVER staged — its plan packed '
                                    'nothing, so no door was ever held for it'),
        'emptied_s': Col(STAMP, 's', 'arm', clock=SIM,
                         null_means='still on site at run end: the detention span is '
                                    'RIGHT-CENSORED, not zero and not missing',
                         note='detention = emptied_s - arrived_s, derived at analysis; a '
                              'censored row bounds it below by the run end'),
        'status':    Col(LABEL, 'enum', 'row',
                         note="'done' | 'discarded' | 'standing', stamped by the door the "
                              'trailer left through. Re-deriving it from the null pattern '
                              'is the read this tag exists to stop: "never staged" and '
                              '"dropped" are the same nulls today and would not be the day '
                              'a staged trailer is dropped'),
    },
    'yard_drains': {
        'run_id':               _KEY,
        'batch':                _KEY,
        'yard_start':           Col(LEVEL, 'trailers', 'batch',
                                    note='standing at ctx-freeze, BEFORE the door fill — '
                                         'the contention question is about the moment of '
                                         'choice; with free_doors_start it is the pair'),
        'free_doors_start':     Col(LEVEL, 'doors', 'batch'),
        'yard_end':             Col(LEVEL, 'trailers', 'batch',
                                    note='trailers this drain never reached; with '
                                         'staged_remainder_end it is the BINDING CUT pair, '
                                         'whose additive statistic is a COUNT OF DRAINS '
                                         'with either above zero — never a sum of levels '
                                         '(the recv_cut scar, 101x on one headline)'),
        'staged_remainder_end': Col(LEVEL, 'units', 'batch', account=PACKS,
                                    note='units left on staged trailers — a different noun '
                                         'from yard_end, which counts vehicles'),
    },
    # ── the SITE dock's own per-batch totals (site-dock 15 section 7, built by 25) ──
    # Four scalars with the same names, the same units and the same accounts as
    # `batch_stats`' receiving block -- deliberately, because they are the SAME quantities
    # ONE SCOPE UP: `batch_stats.recv_*` is one channel leaf's SHARE of a site day,
    # partitioned back by SKU (ADR-0005); these are the site dock's own counters for that
    # day.  Rows exist only in a coupled unit's site inbound DB (the run tree's
    # `site_inbound_db`), and the SCOPE IS THE FILE (site-dock 03) rather than a column
    # here -- site-ness is not a grain, so nothing is added to `Col`.
    'site_receiving': {
        'run_id':        _KEY,
        'batch':         _KEY,
        'recv_depth':    Col(LEVEL, 'units', 'batch', account=PACKS,
                             note='the SITE dock floor; zero on every standing-yard run, '
                                  'where merchandise waits on a trailer rather than on the '
                                  'floor -- written out because that is the model saying '
                                  'so, not a licence to assume it'),
        'recv_unloaded': Col(FLOW, 'units', 'batch', account=PACKS,
                             note='UNLOADS ONLY. A repack is receiving work priced at the '
                                  'dock and charged into recv_seconds, but no `unloaded` '
                                  'counter counts it -- so the closure against the leaves\' '
                                  'rows filters event_type=receive, while the SECONDS '
                                  'closure does not'),
        'recv_cut':      Col(LEVEL, 'units', 'batch', account=PACKS,
                             note='the site twin of batch_stats.recv_cut and it carries the '
                                  'same scar: it re-counts the whole standing dock every '
                                  'batch, so the additive statistic is the count of batches '
                                  'non-zero, never the sum'),
        'recv_seconds':  Col(FLOW, 's', 'batch', clock=SIM,
                             note='the SITE\'s receiving labour for this day, repacks '
                                  'INCLUDED (Dock.charge accrues both). Sums to the two '
                                  'leaves\' work_events receive durations -- the one check '
                                  'that can see a pack lost at site level'),
    },
    'shift_days': {
        'run_id':         _KEY,
        'day':            _KEY,
        'cap_end':        Col(STAMP, 's', 'arm', clock=SIM,
                              note="the day's whistle on the arm's absolute axis"),
        'end_s':          Col(STAMP, 's', 'arm', clock=SIM,
                              note='the drain instant or the cap, whichever came first '
                                   '(timeline.shift_end); < cap_end only when drained'),
        'drained':        Col(LABEL, 'flag', 'row',
                              note='1 = nothing cut, no LABOUR standing at close-out (put + '
                                   'dock + carry_labour; the supply carry is stock, not '
                                   'labour -- equilibrium.is_drained) and no task finished '
                                   'past cap_end (overtime, since 2026-09-07; the '
                                   'shift_day_frame query serves a row stamped before that '
                                   'with the term folded in); 0 = CAPPED, "declared '
                                   'throughput not delivered". The equilibrium check reads '
                                   'this per day, never a sum'),
        'standing':       Col(LEVEL, 'units', 'row',
                              note='a MIXED account — standing_put + standing_dock (packs) + '
                                   'standing_carry (pieces) — kept because "was anything '
                                   "standing\" is the drained verdict's question; read the "
                                   'three parts for honest units. Never summed across days'),
        'standing_put':   Col(LEVEL, 'units', 'row', account=PACKS,
                              note='put queues + held items at close-out'),
        'standing_dock':  Col(LEVEL, 'units', 'row', account=PACKS,
                              note='storage units on the dock floor at close-out'),
        'standing_carry': Col(LEVEL, 'units', 'row', account=PIECES,
                              note='demand rolled into the next batch = labour + supply'),
        'standing_carry_labour': Col(LEVEL, 'units', 'row', account=PIECES,
                                     note="the cut's own carry (unpicked_daycut): standing "
                                          'LABOUR, the only carry the drained verdict reads. '
                                          "A LEVEL at close-out = the day's LAST batch's "
                                          '`carryover` flow; equals the day\'s flow only under '
                                          'one batch per day. The equilibrium check\'s labour '
                                          'clause reads the flows and cross-checks this. '
                                          'NULL on the pre-split vintage 487a65bf83a9'),
        'standing_carry_supply': Col(LEVEL, 'units', 'row', account=PIECES,
                                     note="the shelf's carry (unpicked_unavailable + "
                                          'unpicked_unstocked): stock not delivered, judged '
                                          "by the check's supply clause off the `carryover` "
                                          'flows, never by the drained verdict. A LEVEL, the '
                                          "last batch's flow, and it cannot say which units "
                                          'are re-attempts. NULL on the pre-split vintage'),
        'last_finish':    Col(STAMP, 's', 'arm', clock=SIM,
                              note='the latest crew clock in the day; > cap_end is START-gate '
                                   'overtime'),
    },
    'task_stats': {
        'id':               _KEY,
        'run_id':           _KEY,
        'batch_id':         _KEY,
        'aisle_id':         _KEY,
        'picker_id':        Col(LABEL, 'local-id', 'row', space='crew-local'),
        'task_start_time':  Col(STAMP, 's', 'batch', clock=SIM),
        'task_end_time':    Col(STAMP, 's', 'batch', clock=SIM),
        'duration':         Col(SPAN, 's', 'row', clock=SIM),
        'W':                Col(SCORE, 's', 'row', clock=SIM,
                                note='the labour score the partitioner minimises'),
        'lift_sum':         Col(SCORE, 's-weighted', 'row'),
        'num_bins_visited': Col(COUNT, 'bins', 'row', pair='bins_realized',
                                note='the PLAN — agrees with the actual everywhere until '
                                     'a clamp or cut bites'),
        'total_items':      Col(COUNT, 'items', 'row', account=PIECES,
                                pair='items_realized', note='the PLAN'),
        'items_realized':   Col(COUNT, 'items', 'row', account=PIECES,
                                pair='total_items',
                                note='the ACTUAL, read off the event stream'),
        'bins_realized':    Col(COUNT, 'bins', 'row', pair='num_bins_visited',
                                note='the ACTUAL'),
        'is_outlier':       Col(LABEL, 'flag', 'row'),
    },
}

KEYFRAMES_SEMANTICS: dict = {
    'bin_keyframe': {
        'run_id':       _KEY,
        'batch_id':     _KEY,
        'aisle_id':     _KEY,
        'bayX':         _BAY,
        'bayY':         _BAY,
        'sku':          _KEY,
        'unit_type':    _ENUM,
        'storage_size': _ENUM,
        'qty':          Col(LEVEL, 'items', 'batch', account=PIECES,
                            note='a snapshot per (run, batch, bin) — audit points, never '
                                 'summed across batches'),
    },
    'schema_meta': {
        'key':   Col(LABEL, 'enum', 'row'),
        'value': Col(LABEL, 'text', 'row',
                     null_means='a flag-style key with no payload'),
    },
}

#: Every table of both families — the convention pass left no remainder.
COVERED: tuple = tuple(SIM_DB_SEMANTICS)
KEYFRAMES_COVERED: tuple = tuple(KEYFRAMES_SEMANTICS)

register('sim_db', SIM_DB_SEMANTICS, COVERED)
register('keyframes_db', KEYFRAMES_SEMANTICS, KEYFRAMES_COVERED)
