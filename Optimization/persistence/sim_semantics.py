"""sim_semantics — the sim_db family's column semantics, declared beside its DDL.

The structured form of every warning `Picking_Data.py`'s DDL comments carry in prose: kind,
unit, unit of account, grain, clock, null-meaning and the scar that earned each tag.  Keyed
exactly as `SIM_DB_FAMILY.declared_shape()['tables']` keys its columns, so the completeness
gate (`Tests/architecture/test_column_semantics.py`) is a dict diff with nothing to drift.

`COVERED` names the tables whose tagging is COMPLETE and therefore gated.  It starts with
the four epicenter tables — five of the twelve recorded incident classes hit `batch_stats`
alone — and grows to the whole family in the convention pass ("leave no remainder").

Tags are metadata: importing this module changes no DDL, no shape id, no result.
"""
from __future__ import annotations

from Schema.semantics import (
    BATCHES, ByDiscriminator, Col, COUNT, FLOW, LABEL, LEVEL, PACKS, PIECES, RATE, SCORE,
    SHARE, SIM, SPAN, STAMP, register)

_KEY = Col(LABEL, 'id', 'row')

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
        'queue':      Col(LABEL, 'enum', 'row'),
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
        'actor_uid':   Col(LABEL, 'uid', 'row',
                           note='unique ACROSS crews; a uid in a local slot lands inside '
                                '[0,k) and is accepted — the id-space confusion '
                                '_group_events_by_picker now raises on'),
        'actor_local': Col(LABEL, 'local-id', 'row',
                           note='dense PER CREW [0,k); the other id space'),
        'role':        Col(LABEL, 'enum', 'row'),
        'mode':        Col(LABEL, 'enum', 'row'),
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
}

#: The gate's scope: tables whose tagging is COMPLETE.  Grows to the whole family (and the
#: other families) in the convention pass.
COVERED: tuple = ('batch_stats', 'carryover', 'put_queue_state', 'work_events')

register('sim_db', SIM_DB_SEMANTICS, COVERED)
