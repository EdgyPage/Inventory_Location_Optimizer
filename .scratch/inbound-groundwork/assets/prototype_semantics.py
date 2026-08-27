"""PROTOTYPE -- throwaway, answers wayfinder ticket 05. Not production. Delete freely.

Question: where do per-column semantic tags live, what does an analysis call site look like
reading through the layer, and what does the completeness gate check?
Branch note: the prototype skill's LOGIC branch adapted to a runnable API stub (the question
is a developer-facing interface, not a state machine); each demo prints its full outcome.

Run:  python .scratch/inbound-groundwork/assets/prototype_semantics.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# entry-script bootstrap (legal sys.path.insert site per CLAUDE.md)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

# -- 1. the vocabulary (ticket 03, decided) ---------------------------------------
STAMP, SPAN, LEVEL, FLOW, COUNT, RATE, SCORE, SHARE, LABEL = (
    'stamp', 'span', 'level', 'flow', 'count', 'rate', 'score', 'share', 'label')
SIM, WALL, BATCH_CLOCK = 'sim', 'wall', 'batches'      # clocks
PACKS, PIECES, ITEMS = 'packs', 'pieces', 'items'      # units of account


@dataclass(frozen=True)
class Col:
    """One column's declared semantics. kind+unit+grain mandatory; rest conditional."""
    kind: str
    unit: str
    grain: str
    clock: str | None = None        # mandatory when unit is a time unit
    account: str | None = None      # mandatory on counts of goods
    per: str | None = None          # a RATE's denominator, part of its meaning
    null_means: str | None = None   # mandatory when column is nullable
    logical: str | None = None      # honest name at the logical layer (physical stays frozen)


@dataclass(frozen=True)
class ByDiscriminator:
    """Kind depends on a sibling column's VALUE (carryover.qty by reason)."""
    column: str
    cases: dict = field(default_factory=dict)   # value -> Col


# -- 2. where tags live: one SEMANTICS dict per family, beside the DDL ------------
# (In the real layer this sits in Optimization/persistence/ next to the *_DDL constants,
#  keyed identically to declared_shape(), so the completeness gate is a dict diff.)
SEMANTICS = {
    'batch_stats': {
        'duration':           Col(SPAN,  's', 'batch', clock=SIM),
        'task_makespan':      Col(SPAN,  's', 'batch', clock=SIM),
        'thr_batch':          Col(RATE,  'items/s', 'batch', clock=SIM, per='makespan'),
        'batch_start_time':   Col(STAMP, 's', 'arm', clock=SIM),
        'reorder_placements': Col(COUNT, 'units', 'batch', account=PIECES),
        'queue_depth':        Col(LEVEL, 'units', 'batch', account=PACKS),
        'recv_depth':         Col(LEVEL, 'units', 'batch', account=PACKS),
        'in_transit_qty':     Col(LEVEL, 'units', 'batch', account=PIECES),
        'recv_cut':           Col(LEVEL, 'units', 'batch', account=PACKS),
        'recv_unloaded':      Col(FLOW,  'units', 'batch', account=PACKS),
        'work_day':           Col(LABEL, 'day',   'batch', logical='release_day'),
        'released_late':      Col(SPAN,  's', 'batch', clock=SIM),
    },
    'carryover': {
        'qty': ByDiscriminator('reason', {
            'dock':          Col(LEVEL, 'units', 'batch', account=PACKS),
            'unplaced':      Col(LEVEL, 'units', 'batch', account=PACKS),
            'held':          Col(LEVEL, 'units', 'batch', account=PACKS),
            'unpicked_cut':  Col(FLOW,  'units', 'batch', account=PIECES),
        }),
    },
    'runtime': {
        'total_s':    Col(SPAN, 's', 'arm', clock=WALL),
        'gc_pause_s': Col(SPAN, 's', 'arm', clock=WALL),  # worker lifetime, not batch loop
    },
}


class SemanticsError(Exception):
    pass


# -- 3. the call site: a frame that knows its columns' semantics ------------------
class SemFrame:
    """What analysis code holds after a named-query read. Physical OR logical names resolve."""

    def __init__(self, table, rows):
        self.table, self.rows, self.sem = table, rows, SEMANTICS[table]
        self._logical = {c.logical: p for p, c in self.sem.items()
                         if isinstance(c, Col) and c.logical}

    def _col(self, name, row=None):
        phys = self._logical.get(name, name)
        tag = self.sem[phys]
        if isinstance(tag, ByDiscriminator):
            if row is None:
                raise SemanticsError('%s.%s: kind depends on %r; row-free aggregation is '
                                     'not defined for it' % (self.table, phys, tag.column))
            tag = tag.cases[row[tag.column]]
        return phys, tag

    def sum(self, name):
        phys, tag = self._col(name)
        if tag.kind not in (FLOW, COUNT):
            raise SemanticsError(
                'SUM(%s.%s) refused: kind=%s is a standing quantity re-measured each '
                'snapshot, not an additive flow. (This exact read published a 101x wrong '
                'headline once.) Additive statistic for a level: count of rows non-zero.'
                % (self.table, phys, tag.kind.upper()))
        return sum(r[phys] for r in self.rows)

    def hours(self, name):
        phys, tag = self._col(name)
        if tag.kind != SPAN:
            raise SemanticsError('hours(%s): kind=%s, not a span' % (phys, tag.kind))
        return sum(r[phys] for r in self.rows) / 3600.0   # real layer: timeline.SECONDS_PER_HOUR

    def ratio(self, a, b):
        (pa, ta), (pb, tb) = self._col(a), self._col(b)
        if ta.clock != tb.clock:
            raise SemanticsError('%s/%s refused: clocks differ (%s vs %s) - same word '
                                 '"seconds", different instruments' % (pa, pb, ta.clock, tb.clock))
        return sum(r[pa] for r in self.rows) / sum(r[pb] for r in self.rows)

    def add_levels(self, a, b):
        (pa, ta), (pb, tb) = self._col(a), self._col(b)
        if ta.account != tb.account:
            raise SemanticsError('%s+%s refused: units of account differ (%s vs %s) - packs '
                                 'are not pieces' % (pa, pb, ta.account, tb.account))
        return [r[pa] + r[pb] for r in self.rows]


# -- 4. the completeness gate: declared shape minus SEMANTICS keys ----------------
def check_completeness(shape, semantics):
    missing = []
    for table, cols in shape.items():
        tagged = semantics.get(table, {})
        for col in cols:
            if col not in tagged:
                missing.append('%s.%s' % (table, col))
    return missing


# -- 5. use-assertions: a Requires that also declares HOW it reads ----------------
def validate_uses(label, uses):
    """uses: {'table.column': 'sum'|'ratio'|'read'} -> readable refusal clauses."""
    clauses = []
    for key, how in uses.items():
        table, col = key.split('.')
        tag = SEMANTICS.get(table, {}).get(col)
        if isinstance(tag, ByDiscriminator):
            clauses.append('[%s] %s: aggregate declared %r but kind depends on %r - declare '
                           'per-%s uses instead' % (label, key, how, tag.column, tag.column))
        elif tag and how == 'sum' and tag.kind not in (FLOW, COUNT):
            clauses.append('[%s] %s: declares SUM over a %s'
                           % (label, key, tag.kind.upper()))
    return clauses


# -- demos ------------------------------------------------------------------------
ROWS = [
    {'duration': 2262.0, 'task_makespan': 9048.0, 'recv_cut': 3050, 'recv_unloaded': 210,
     'reorder_placements': 118, 'queue_depth': 410, 'recv_depth': 3050,
     'in_transit_qty': 5210, 'work_day': 0, 'released_late': 0.0,
     'batch_start_time': 0.0, 'thr_batch': 1.4},
    {'duration': 2101.0, 'task_makespan': 8404.0, 'recv_cut': 3055, 'recv_unloaded': 195,
     'reorder_placements': 96, 'queue_depth': 388, 'recv_depth': 3055,
     'in_transit_qty': 5002, 'work_day': 0, 'released_late': 118.5,
     'batch_start_time': 2262.0, 'thr_batch': 1.5},
]


def demo(n, title, fn):
    print('\n[%d] %s' % (n, title))
    try:
        print('    OK:', fn())
    except SemanticsError as e:
        print('    REFUSED:', e)


def main():
    f = SemFrame('batch_stats', ROWS)
    print('=== semantic-layer accessor prototype (throwaway) ===')

    demo(1, 'sum a FLOW (reorder_placements) - allowed', lambda: f.sum('reorder_placements'))
    demo(2, 'sum a LEVEL (recv_cut) - the 101x incident', lambda: f.sum('recv_cut'))
    demo(3, 'hours of a SPAN via declared unit', lambda: round(f.hours('task_makespan'), 2))
    demo(4, 'logical rename: release_day resolves physical work_day',
         lambda: f._col('release_day')[0])
    demo(5, 'add packs to pieces (queue_depth + in_transit_qty)',
         lambda: f.add_levels('queue_depth', 'in_transit_qty'))
    demo(6, 'value-dependent kind: row-free SUM over carryover.qty',
         lambda: SemFrame('carryover', []).sum('qty'))

    print('\n[7] use-assertions at declaration time (staged gate, phase 2):')
    for clause in validate_uses('receiving_report', {
            'batch_stats.recv_cut': 'sum', 'batch_stats.recv_unloaded': 'sum',
            'carryover.qty': 'sum'}):
        print('    ', clause)

    print('\n[8] completeness gate against the REAL declared sim_db shape:')
    try:
        from Optimization.persistence.Picking_Data import SIM_DB_FAMILY
        raw = SIM_DB_FAMILY.declared_shape()['tables']          # {'tables': {t: {'columns': ...}}}
        shape = {t: [c['name'] for c in spec['columns']] for t, spec in raw.items()}
    except Exception as e:   # prototype: fall back to a frozen sliver of the census
        print('     (real import unavailable here: %s -- using embedded sliver)' % e)
        shape = {'batch_stats': list(SEMANTICS['batch_stats']) + ['thr_task', 'num_tasks'],
                 'carryover': ['qty', 'reason']}
    missing = check_completeness(shape, SEMANTICS)
    print('     tables in shape: %d; untagged columns: %d' % (len(shape), len(missing)))
    print('     first few untagged:', missing[:6])
    print('     (phase-1 gate = this list must be EMPTY; today it shows the work remaining)')


if __name__ == '__main__':
    main()
