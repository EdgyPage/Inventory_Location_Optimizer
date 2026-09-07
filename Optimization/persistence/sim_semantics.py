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
        'is_outlier':         Col(LABEL, 'flag', 'batch'),
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
                           note='for put/receive it equals role; for pick a row is a state '
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
        'lift_sum':      Col(SCORE, 's-weighted', 'batch',
                             note='digest-stored float; summation order is locked'),
        'pick_load_sum': Col(FLOW, 'items', 'batch', account=PIECES),
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
                              note="the legacy countdown ('lead' rows only); the trailer "
                                   "feature replaces it flag-on with absolute-clock leads"),
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
                                   null_means='not recorded by this vintage'),
        'reorder_point':       Col(COUNT, 'items', 'sku', account=PIECES,
                                   null_means='not recorded by this vintage'),
        'lead_time_mean':      Col(SPAN, 'batches', 'sku', clock=BATCHES,
                                   null_means='not recorded by this vintage',
                                   note='the batch-denominated lead the trailer feature '
                                        'replaces flag-on'),
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
    'shift_days': {
        'run_id':         _KEY,
        'day':            _KEY,
        'cap_end':        Col(STAMP, 's', 'arm', clock=SIM,
                              note="the day's whistle on the arm's absolute axis"),
        'end_s':          Col(STAMP, 's', 'arm', clock=SIM,
                              note='the drain instant or the cap, whichever came first '
                                   '(timeline.shift_end); < cap_end only when drained'),
        'drained':        Col(LABEL, 'flag', 'row',
                              note='1 = nothing cut and no LABOUR standing at close-out (put + '
                                   'dock + carry_labour; the supply carry is stock, not '
                                   'labour -- equilibrium.is_drained); 0 = CAPPED, "declared '
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
                                          'NULL on the pre-split vintage 487a65bf83a9'),
        'standing_carry_supply': Col(LEVEL, 'units', 'row', account=PIECES,
                                     note="the shelf's carry (unpicked_unavailable + "
                                          'unpicked_unstocked): stock not delivered, judged '
                                          'by missed share, never by the drained verdict. '
                                          'NULL on the pre-split vintage'),
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
