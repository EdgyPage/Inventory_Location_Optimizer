"""receiving_report.py — the receiving stream, reconciled against the run that wrote it.

# ── why this exists at all ────────────────────────────────────────────────────────

`work_events` has no consumer. Outside `Tests/`, the only modules that mention it are the
config, the metrics writer, the persistence layer and the runner — nothing in
`Visualization/` or `Performance_Evaluations/` reads a single row. So a third work stream can
be wired end to end, write duplicated or malformed rows, and produce a run that looks
completely healthy from every existing angle.

That makes the receiving crew unfalsifiable unless something reads it back. This is that
something, and it is deliberately a RECONCILIATION rather than a report: it compares two
surfaces that were written by different code from different state, so agreement is evidence
and disagreement names which half is wrong.

  `work_events`   one row per unload, written by `metrics.work_events.recv_rows` from the
                  dock's own drained records, stamped against the crew's absolute carry.
  `batch_stats`   four scalars per batch, written by `persistence.Picking_Data` from
                  `Inventory_Manager.receiving_snapshot()`, which reads the dock's counters.

Nothing forces those to agree. They share no code below the manager, and each is a plausible
number on its own.

# ── the five checks, and the specific defect each one catches ─────────────────────

1. **Seconds agree.** `SUM(duration) WHERE role='receive'` against `SUM(recv_seconds)`.
   Catches a dropped `we.extend`, a `bs.recv_seconds` assignment that never ran (the skipped
   branch), and a checkpoint buffer that was not cleared — `INSERT OR REPLACE` on a composite
   key makes re-inserted rows look correct, but it doubles this sum.

2. **Counts agree, EXACTLY.** `COUNT(*) WHERE role='receive'` against `SUM(recv_unloaded)`.
   Both are carried integers rather than recomputed floats, so `==` is the stronger statement
   and a tolerance here would be hiding something.

3. **The uid blocks are disjoint and contiguous.** `actor_uid` is the only thing that says
   WHO did a unit of work, and nothing enforces it: `Worker` validates only non-negativity,
   the DDL has no uniqueness constraint, `put_rows` bounds-checks `0 <= widx < len(workers)`
   which a collision passes, and the merged view still sorts. Nothing else in the repo can
   see a collision — it surfaces only as a per-actor rollup quietly merging two people, and
   it is quietest when the receiving crew is small, which is the likely configuration.

4. **No duplicate merge keys.** `(t_abs, batch_id, role, mode, actor_uid, seq)` is the
   declared total order. The existing guard is `assert rows == sorted(rows)`, which any
   non-decreasing sequence satisfies — including one full of identical keys. A genuinely
   ambiguous merge passes the test that exists to prove the order is total.

5. **`role` and `event_type` agree.** `put_rows` takes `role` from the worker and used to
   hard-code the event type, so a receive row could carry `role='receive', event_type='put'`.
   Then `SUM(duration) WHERE role='put'` and the same query on `event_type` disagree, and
   there is nothing to point at. Checked in BOTH directions.

# ── usage ─────────────────────────────────────────────────────────────────────────

    python Diagnostics/receiving_report.py <run_root_or_name>     # every arm, PASS/FAIL
    python Diagnostics/receiving_report.py <run_root> --verbose   # per-arm numbers

Run roots may be bare names (resolved under COMPARISON_OUTPUT_DIR) or absolute paths. Arms
are enumerated through the run's own contract (`runschema.resolver_for`) and never by joining
path strings: the store CONFIG and the store CHANNEL are both named `store`, so
`<cell>/<pair>/store/store/` is a real path, and two levels are conditional.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a script ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: Seconds. Both sides sum the same float durations in the same order, so they should agree
#: bit for bit; this leaves room for a future consumer that rounds on the way in without
#: leaving room for a real discrepancy (one unload is ~1 second).
_TOL = 1e-6


def _open_ro(path: str):
    """Read-only AND immutable: a plain `mode=ro` open still mints -wal/-shm sidecars beside
    an archived DB, and cannot remove them afterwards."""
    return sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)


def _has(con, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def reconcile(db_path: str, run_id: int | None = None) -> dict:
    """Cross-check one sim DB's receiving surface. Returns a dict; never raises on content.

    `run_id` None means every run in the file, which is what a per-arm DB holds anyway.

    A DB with no receiving activity is a PASS with `active=False` — every run before this
    feature, and every run that did not ask for a crew. That is not the same as a failure,
    and conflating them would make the tool useless on exactly the runs it should be quiet
    about.
    """
    out: dict = {'db': os.path.basename(db_path), 'active': False, 'checks': {}, 'verdict': 'PASS'}
    if not os.path.isfile(db_path):
        return {**out, 'verdict': 'MISSING'}

    con = _open_ro(db_path)
    try:
        if not _has(con, 'work_events') or not _has(con, 'batch_stats'):
            return {**out, 'verdict': 'SKIP',
                    'note': 'this vintage predates work_events / the receiving columns'}
        where = '' if run_id is None else ' AND run_id = :rid'
        args = {} if run_id is None else {'rid': run_id}

        n_ev, secs_ev = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(duration), 0.0) FROM work_events "
            f"WHERE role = 'receive'{where}", args).fetchone()
        unloaded, secs_bs, cut, depth = con.execute(
            'SELECT COALESCE(SUM(recv_unloaded), 0), COALESCE(SUM(recv_seconds), 0.0), '
            'COALESCE(SUM(recv_cut), 0), COALESCE(MAX(recv_depth), 0) FROM batch_stats '
            + ('WHERE 1=1' + where if where else 'WHERE 1=1'), args).fetchone()

        out.update(seconds_events=secs_ev, seconds_batch_stats=secs_bs,
                   qty_events=n_ev, qty_batch_stats=unloaded,
                   cut_total=cut, dock_depth_max=depth,
                   active=bool(n_ev or unloaded or cut))

        # ── 1 + 2: the two surfaces ───────────────────────────────────────────────
        out['checks']['seconds_agree'] = abs(secs_ev - secs_bs) <= _TOL
        out['checks']['counts_agree'] = (n_ev == unloaded)

        # ── 3: uid blocks ─────────────────────────────────────────────────────────
        by_role: dict = {}
        for role, uid in con.execute(
                f'SELECT DISTINCT role, actor_uid FROM work_events WHERE 1=1{where}', args):
            by_role.setdefault(role, set()).add(uid)
        overlaps, seen = 0, set()
        for uids in by_role.values():
            overlaps += len(seen & uids)
            seen |= uids
        out['uid_blocks'] = {r: (min(u), max(u)) for r, u in sorted(by_role.items())}
        out['uid_overlaps'] = overlaps
        out['checks']['uids_disjoint'] = (overlaps == 0)
        # Contiguity is a separate claim from disjointness and catches a different mistake:
        # a crew allocated from a hand-picked offset rather than the running cursor.
        out['checks']['uids_contiguous'] = (not seen) or (sorted(seen) == list(range(len(seen))))

        # ── 4: the merge key is actually a key ────────────────────────────────────
        dupes = con.execute(
            f'SELECT COUNT(*) FROM (SELECT 1 FROM work_events WHERE 1=1{where} '
            f'GROUP BY t_abs, batch_id, role, mode, actor_uid, seq HAVING COUNT(*) > 1)',
            args).fetchone()[0]
        out['duplicate_merge_keys'] = dupes
        out['checks']['merge_key_is_total'] = (dupes == 0)

        # ── 5: role and event_type say the same thing ─────────────────────────────
        mism = con.execute(
            f"SELECT COUNT(*) FROM work_events WHERE 1=1{where} AND "
            f"((role = 'receive') != (event_type = 'receive'))", args).fetchone()[0]
        out['role_event_type_mismatches'] = mism
        out['checks']['role_matches_event_type'] = (mism == 0)
    finally:
        con.close()

    out['verdict'] = 'PASS' if all(out['checks'].values()) else 'FAIL'
    return out


def _sim_dbs(root: str):
    """Every arm's sim DB, through the run's own contract."""
    from Optimization.runschema import resolve_base_dir, resolver_for
    base = resolve_base_dir(root)
    rt = resolver_for(base)
    return base, sorted(rt.glob('sim_db'))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('run', help='run root (a path, or a bare name under COMPARISON_OUTPUT_DIR)')
    p.add_argument('--verbose', '-v', action='store_true', help='per-arm numbers')
    a = p.parse_args(argv)

    try:
        base, dbs = _sim_dbs(a.run)
    except Exception as exc:                                   # noqa: BLE001 - reported
        print(f'receiving_report: cannot resolve {a.run!r}: {exc}')
        return 2
    if not dbs:
        print(f'receiving_report: no sim DBs under {base}')
        return 2

    results = [reconcile(d) for d in dbs]
    active = [r for r in results if r.get('active')]
    failed = [r for r in results if r['verdict'] == 'FAIL']

    for r in results:
        if r['verdict'] == 'FAIL' or (a.verbose and r.get('active')):
            bad = [k for k, v in r['checks'].items() if not v]
            print(f"  {r['verdict']:4} {r['db']}")
            if a.verbose:
                print(f"        events={r.get('qty_events', 0):,} "
                      f"batch_stats={r.get('qty_batch_stats', 0):,} "
                      f"secs={r.get('seconds_events', 0.0):,.3f}/"
                      f"{r.get('seconds_batch_stats', 0.0):,.3f} "
                      f"cut={r.get('cut_total', 0):,} depth={r.get('dock_depth_max', 0):,}")
                print(f"        uid blocks: {r.get('uid_blocks')}")
            if bad:
                print(f'        FAILED: {", ".join(bad)}')

    print(f'receiving_report: {len(results)} arm(s), {len(active)} with receiving activity, '
          f'{len(failed)} FAILED')
    if not active:
        print('  (no arm recorded any receiving — this run had no receiving crew, which is '
              'a PASS, not a silence)')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
