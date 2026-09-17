"""state_sources — the four records `state_at` tries, behind one interface.

`SqliteSimReader.state_at` was a hard-coded if-chain over four private methods of a 1,150-line
class, each writing its own SQL against physical tables:

    state = self._state_from_spans(...)                 # the sidecar's log-built span index
    if state is None: state = self._state_from_log(...)  # the live bin_placement/eviction fold
    if state is None: state = self._state_from_keyframes(...)
                              (+ _state_without_keyframes, reached from INSIDE the third)

Four implementations of one contract (`RECONSTRUCTION.md` section 1) selected by an if-chain, with
the fourth hidden inside the third -- so "which records can answer for this run" was a question
you answered by reading a method body. `SOURCES` is that list, in cost order, and `state_at` is a
loop over it.

## `available()` IS NOT PART OF THE INTERFACE, and that is deliberate

Ticket 15 asks for `available(reader) -> bool` beside `state(...) -> dict`. Two questions where
one suffices -- and worse, two that cannot be kept in agreement: `SpanIndexSource` has a sidecar
index (so it is "available") and still returns None when that index holds no row for THIS batch,
which the original method does today and must keep doing. An `available()` that said True there
would be wrong; one that opened the index to find out would do the query twice.

So a source answers exactly once: a frame, or `None` meaning "ask the next one". The ORDER is the
policy, and it lives in `SOURCES` rather than in a chain of `if state is None`.

## The last source must answer

`ArchiveSource` never returns None -- when it has no record either, it returns a frame whose
`note` says so, because "this run carries no spatial record at all" is an answer a viewer can
render and a `None` falling out of the loop is not. `state_at` asserts that rather than trusting
it.

## Why one module and not four

Ticket 15 says "one per adapter module". These four share a base, a helper
(`_apply_picks_upto_t`), and an ORDERING that only means anything when they are read together --
splitting them puts the ordered tuple in a fifth file that imports the other four. One module
holding an interface, its adapters and the table naming them is the shape `checkpoint_buffer.py`
and `leaf_scope.py` already use in this codebase.
"""
from __future__ import annotations

import json
import sqlite3

__all__ = ['StateSource', 'SpanIndexSource', 'LogFoldSource', 'KeyframeSource',
           'ArchiveSource', 'SOURCES']


def _int_list(aisles) -> str:
    """`[1, 2]` -> `'1,2'` — ints only, so this cannot carry anything but numbers."""
    return ','.join(str(int(a)) for a in aisles)


class StateSource:
    """One way to reconstruct the occupied-bin frame at `(batch, t)`.

    Holds the reader rather than subclassing it: these are strategies over one file set, not
    kinds of reader, and the reader's own surface (`run_id`, `sim_db`, `_sql`, the keyframe
    helpers) is what they read through.
    """

    __slots__ = ('_r',)

    #: Is this record exact at EVERY batch, or only at the ones it has a snapshot for? Declared
    #: rather than inferred: it is what `state_at`'s callers key their "approximate" badge on,
    #: and the two frames that are not exact say so in their own `note` as well.
    exact_everywhere = True

    def __init__(self, reader) -> None:
        self._r = reader

    def __repr__(self) -> str:
        return f'<{type(self).__name__}>'

    def state(self, batch: int, aisles, t) -> dict | None:
        """The frame, or None meaning "I cannot answer; ask the next source"."""
        raise NotImplementedError

    # ── shared ────────────────────────────────────────────────────────────────────

    @staticmethod
    def _scope(aisles) -> str:
        return f' AND aisle_id IN ({_int_list(aisles)})' if aisles else ''

    def _apply_picks_upto_t(self, con, bins: dict, batch: int, t, scope: str) -> None:
        """Subtract batch `batch`'s own picks up to sim_time `t`.  No-op when `t` is None.

        This is the intra-batch clock: `picks.sim_time` is the finest resolution the record has,
        and it is unaffected by the log (restocks carry no sub-batch time -- `check_reorders()`
        runs entirely between two batches' simulations).
        """
        if t is None:
            return
        from Visualization.readers.base import _deplete
        for r in con.execute(
                f'SELECT aisle_id, bayX, bayY, SUM(quantity) n FROM picks '
                f'WHERE run_id=? AND batch_id=? AND sim_time<=?{scope} '
                f'GROUP BY aisle_id, bayX, bayY', (self._r.run_id, batch, float(t))):
            _deplete(bins, r['aisle_id'], r['bayX'], r['bayY'], r['n'])


class SpanIndexSource(StateSource):
    """The frame from the sidecar's log-built span index. None = no such index.

    The span gives the bin's SKU and the qty it was filled with at `t_from`; the qty NOW is that
    minus the picks since.  A keyframe at or below `batch` is used as the qty anchor whenever one
    exists -- not for correctness, but to bound the pick scan to one keyframe interval instead of
    the whole run.  Without keyframes the answer is the same, read from a longer range.
    """

    __slots__ = ()

    def state(self, batch: int, aisles, t) -> dict | None:
        from Visualization.readers.base import _deplete, _ro
        r_ = self._r
        cache = r_._span_index()
        if cache is None:
            return None
        scope = self._scope(aisles)
        try:
            rows = cache.execute(
                r_._sql('viz_cache_db', 'bin_span_scoped'),
                {'run_id': r_.run_id, 'batch': batch,
                 'aisles': json.dumps([int(a) for a in aisles]) if aisles else None}).fetchall()
        except sqlite3.Error:
            return None
        finally:
            cache.close()
        if not rows:
            # Either the batch is outside the cached run or the scope holds no occupied bin.
            # An empty warehouse is not a state this simulation produces, so fall through
            # rather than assert an exact empty frame.  THIS is why `available()` is not part
            # of the interface: the index exists and still cannot answer.
            return None

        kf = r_._keyframe_at_or_before(batch)
        kf_state = r_._keyframe_state(kf, aisles) if kf is not None else {}
        bins: dict[str, dict] = {}
        anchor: dict[str, int] = {}                   # bin -> batch its qty is known at
        for r in rows:
            if r['sku'] is None:
                continue
            key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
            sku, t_from = int(r['sku']), int(r['t_from'])
            known = kf_state.get(key)
            # The keyframe only anchors a span it actually falls inside, and only when the two
            # agree on the SKU: a disagreement means one of the records is wrong, and the log is
            # the one this frame is built from.
            if kf is not None and t_from <= kf and known is not None and known['sku'] == sku:
                bins[key] = {'sku': sku, 'qty': known['qty']}
                anchor[key] = kf
            else:
                bins[key] = {'sku': sku, 'qty': int(r['qty_at_from'])}
                anchor[key] = t_from

        lo = min(anchor.values())
        con = _ro(r_.sim_db)
        try:
            if batch > lo:
                # Grouped per (bin, batch) rather than per bin: each bin subtracts only the
                # picks at or after ITS OWN anchor -- earlier ones belong to a previous unit.
                for r in con.execute(
                        f'SELECT aisle_id, bayX, bayY, batch_id, SUM(quantity) n FROM picks '
                        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{scope} '
                        f'GROUP BY aisle_id, bayX, bayY, batch_id', (r_.run_id, lo, batch)):
                    key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
                    if int(r['batch_id']) >= anchor.get(key, batch):
                        _deplete(bins, r['aisle_id'], r['bayX'], r['bayY'], r['n'])
            self._apply_picks_upto_t(con, bins, batch, t, scope)
        finally:
            con.close()
        return {'batch': batch, 'keyframe': kf, 't': t, 'bins': bins,
                'exact': True, 'restocks_pending': 0, 'note': ''}


class LogFoldSource(StateSource):
    """The frame folded live from `bin_placement` + `bin_eviction` + `picks`.

    Used when the run HAS a log but no fresh sidecar. The nearest keyframe is the base and the
    log carries it forward, so the work is bounded by the keyframe interval. With no keyframe
    below, an empty warehouse is the base -- valid only when the log starts at batch 0, i.e. it
    recorded the initial fill; a resumed run whose log starts later cannot be anchored and falls
    through.
    """

    __slots__ = ()

    def state(self, batch: int, aisles, t) -> dict | None:
        from Visualization.readers.base import _deplete, _ro
        r_ = self._r
        start = r_._log_start()
        if start is None:
            return None
        kf = r_._keyframe_at_or_before(batch)
        if kf is None and start != 0:
            return None
        base = kf if kf is not None else 0
        scope = self._scope(aisles)

        bins = r_._keyframe_state(kf, aisles) if kf is not None else {}
        events: dict[int, list] = {}          # batch -> [(kind, seq, key, sku, qty, topup)]
        con = _ro(r_.sim_db)
        try:
            # kind 0 = EVICT, 1 = PLACE, so sorting a batch's events replays the runner's order:
            # the reloader, then check_reorders, then the picks.
            # Gated on the VINTAGE's surface, not on bin_placement's probe: a vintage carrying
            # placements without the eviction table used to raise `no such table` out of
            # state_at here, while precompute guarded the same read.  (The bug fix.)
            if r_._has('sim_db', 'bin_eviction'):
                for r in con.execute(
                        f'SELECT batch_id, seq, aisle_id, bayX, bayY FROM bin_eviction '
                        f'WHERE run_id=? AND batch_id>? AND batch_id<=?{scope}',
                        (r_.run_id, base, batch)):
                    events.setdefault(int(r['batch_id']), []).append(
                        (0, int(r['seq']), f"{r['aisle_id']},{r['bayX']},{r['bayY']}",
                         None, 0, 0))
            # ADR-0003's own-bin rung ADDS to a bin rather than filling an empty one, and the
            # fold below has to know which.  A vintage without the column had no such rung, so
            # a literal 0 there is the TRUE answer, not a default hiding missing data.
            _topup = ("CASE bin_state WHEN 'occupied' THEN 1 ELSE 0 END"
                      if r_._has_col('sim_db', 'bin_placement', 'bin_state') else '0')
            for r in con.execute(
                    f'SELECT batch_id, seq, aisle_id, bayX, bayY, sku, qty, {_topup} AS topup '
                    f'FROM bin_placement '
                    f'WHERE run_id=? AND batch_id>? AND batch_id<=?{scope}',
                    (r_.run_id, base, batch)):
                events.setdefault(int(r['batch_id']), []).append(
                    (1, int(r['seq']), f"{r['aisle_id']},{r['bayX']},{r['bayY']}",
                     int(r['sku']), int(r['qty']), int(r['topup'])))
            picks: dict[int, list] = {}
            if batch > base:
                for r in con.execute(
                        f'SELECT batch_id, aisle_id, bayX, bayY, SUM(quantity) n FROM picks '
                        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{scope} '
                        f'GROUP BY batch_id, aisle_id, bayX, bayY', (r_.run_id, base, batch)):
                    picks.setdefault(int(r['batch_id']), []).append(
                        (r['aisle_id'], r['bayX'], r['bayY'], r['n']))

            for b in range(base, batch + 1):
                for kind, _seq, key, sku, qty, topup in sorted(events.get(b, ())):
                    if kind:
                        if topup:
                            # A TOP-UP ADDS: `qty` is the increment, not the bin's contents.
                            cur = bins.get(key)
                            bins[key] = {'sku': sku,
                                         'qty': (cur['qty'] if cur else 0) + qty}
                        else:
                            bins[key] = {'sku': sku, 'qty': qty}
                    else:
                        bins.pop(key, None)
                for aisle, bx, by, n in picks.get(b, ()):
                    _deplete(bins, aisle, bx, by, n)
            self._apply_picks_upto_t(con, bins, batch, t, scope)
        finally:
            con.close()
        return {'batch': batch, 'keyframe': kf, 't': t, 'bins': bins,
                'exact': True, 'restocks_pending': 0, 'note': ''}


class KeyframeSource(StateSource):
    """The pre-log frame: nearest keyframe below, minus the picks since.

    Exact when `batch` IS a keyframe batch.  Otherwise depletion-exact but missing the restocks
    of the batches in between, which is reported rather than hidden.
    """

    __slots__ = ()
    exact_everywhere = False

    def state(self, batch: int, aisles, t) -> dict | None:
        from Visualization.readers.base import _deplete, _ro
        r_ = self._r
        kf = r_._keyframe_at_or_before(batch)
        scope = self._scope(aisles)

        if kf is None:
            kfs = r_.keyframe_batches()
            if kfs:
                # Keyframes exist but all come AFTER this batch -- reachable on a resumed run.
                # Saying "no keyframes in this run" here would be false, and returning the
                # delta-rebuilt frame silently would be worse.
                return {'batch': batch, 'keyframe': None, 't': t, 'bins': {},
                        'exact': False, 'restocks_pending': r_._pending_restocks(0, batch),
                        'note': f'batch {batch} precedes the first keyframe ({kfs[0]}); no exact '
                                f'spatial frame exists below it'}
            # No keyframes at all (keyframe_interval=0) AND no log.  FALL THROUGH to
            # `ArchiveSource` -- which used to be a call from inside this method, so "there is a
            # fourth record" was a fact you learned by reading this body.
            return None

        bins = r_._keyframe_state(kf, aisles)

        # Apply picks: whole batches [kf, batch), then batch itself up to t.
        con = _ro(r_.sim_db)
        try:
            if batch > kf:
                for r in con.execute(
                        f'SELECT aisle_id, bayX, bayY, SUM(quantity) n FROM picks '
                        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{scope} '
                        f'GROUP BY aisle_id, bayX, bayY', (r_.run_id, kf, batch)):
                    _deplete(bins, r['aisle_id'], r['bayX'], r['bayY'], r['n'])
            self._apply_picks_upto_t(con, bins, batch, t, scope)
        finally:
            con.close()

        pending = r_._pending_restocks(kf, batch)
        return {
            'batch': batch, 'keyframe': kf, 't': t,
            'bins': bins,
            'exact': batch == kf,
            'restocks_pending': pending,
            'note': ('' if batch == kf else
                     f'frame built from keyframe {kf}; at least {pending} restock placements '
                     f'between batches {kf + 1} and {batch} are not represented'),
        }


class ArchiveSource(StateSource):
    """Last-resort path: an ARCHIVED run written with keyframe_interval=0.  NEVER returns None.

    Still reachable, and not dead code: it is the only record such an arm has.  A run from this
    build never lands here twice over -- it has a log, so `LogFoldSource` folds it from an empty
    warehouse exactly, and `bin_inventory` is not even in its schema.

    `bin_inventory` (archive-only; no longer written) holds one full snapshot at the run's FIRST
    batch -- not necessarily 0 on a resumed run -- and depletion-only deltas after it.  Ordering
    is by `batch_id, id`: the loop is last-write-wins and a bin can receive rows from two
    branches in one batch, so `batch_id` alone is not a total order.

    The table's ABSENCE is a normal outcome, not an error: it means the file is new enough to have
    no such record and old enough (keyframe_interval=0) to have no keyframe either.  Say so,
    rather than raising `no such table` at a caller three layers up -- which is also why this
    source answers unconditionally and `state_at`'s loop can rely on a frame coming back.
    """

    __slots__ = ()
    exact_everywhere = False

    def state(self, batch: int, aisles, t) -> dict:
        from Visualization.readers.base import _ro
        r_ = self._r
        scope = self._scope(aisles)
        bins: dict[str, dict] = {}
        con = _ro(r_.sim_db)
        try:
            base = con.execute('SELECT MIN(batch_id) b FROM bin_inventory WHERE run_id=?',
                               (r_.run_id,)).fetchone()['b']
            for r in con.execute(
                    f'SELECT aisle_id, bayX, bayY, sku, post_qty FROM bin_inventory '
                    f'WHERE run_id=? AND batch_id<=?{scope} ORDER BY batch_id, id',
                    (r_.run_id, batch)):
                key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
                if r['post_qty'] > 0:
                    bins[key] = {'sku': int(r['sku']), 'qty': int(r['post_qty'])}
                else:
                    bins.pop(key, None)
        except sqlite3.OperationalError:              # no bin_inventory -> nothing to rebuild from
            return {
                'batch': batch, 'keyframe': None, 't': t, 'bins': {}, 'exact': False,
                'restocks_pending': r_._pending_restocks(0, batch),
                'note': 'this run carries no spatial record at all: no keyframes '
                        '(keyframe_interval=0), no bin-mutation log, and no bin_inventory. '
                        'Re-run the arm to get one.',
            }
        finally:
            con.close()
        return {
            'batch': batch, 'keyframe': None, 't': t, 'bins': bins,
            # `t` is not applied on this path, so a frame carrying one is never exact.
            'exact': base is not None and batch == base and t is None,
            'restocks_pending': r_._pending_restocks(base or 0, batch),
            'note': 'no keyframes in this run; rebuilt from depletion deltas, which cannot '
                    'show restocks. Re-run with --keyframe-interval > 0 for exact frames.',
        }


#: THE ORDER IS THE POLICY: cheapest first, and the last one must answer.
#:
#: It is `RECONSTRUCTION.md` section 1's cost order, and it was four `if state is None` lines
#: plus a call buried inside the third method.  Reordering this tuple changes which record a
#: viewer sees for a run that has more than one, which is a behaviour change and not tidying.
SOURCES: tuple = (SpanIndexSource, LogFoldSource, KeyframeSource, ArchiveSource)
