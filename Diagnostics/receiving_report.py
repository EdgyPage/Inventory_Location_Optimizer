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

# ── the six checks, and the specific defect each one catches ──────────────────────

1. **Seconds agree.** `SUM(duration) WHERE role='receive'` against `SUM(recv_seconds)`.
   Catches a dropped `we.extend`, a `bs.recv_seconds` assignment that never ran (the skipped
   branch), and a checkpoint buffer that was not cleared — `INSERT OR REPLACE` on a composite
   key makes re-inserted rows look correct, but it doubles this sum.

2. **Counts agree, EXACTLY.** `COUNT(*) WHERE role='receive'` against `SUM(recv_unloaded)`.
   Both are carried integers rather than recomputed floats, so `==` is the stronger statement
   and a tolerance here would be hiding something.

3. **The uid blocks are disjoint.** `actor_uid` is the only thing that says
   WHO did a unit of work, and nothing enforces it: `Worker` validates only non-negativity,
   the DDL has no uniqueness constraint, `put_rows` bounds-checks `0 <= widx < len(workers)`
   which a collision passes, and the merged view still sorts. Nothing else in the repo can
   see a collision — it surfaces only as a per-actor rollup quietly merging two people, and
   it is quietest when the receiving crew is small, which is the likely configuration.
   Contiguity is deliberately NOT checked -- an allocated-but-idle crew leaves the same gap
   as a misallocated one, so the check failed healthy runs and passed the defect it was for.

4. **No duplicate merge keys.** `(t_abs, batch_id, role, mode, actor_uid, seq)` is the
   declared total order. The existing guard is `assert rows == sorted(rows)`, which any
   non-decreasing sequence satisfies — including one full of identical keys. A genuinely
   ambiguous merge passes the test that exists to prove the order is total.

5. **`role` and `event_type` agree.** `put_rows` takes `role` from the worker and used to
   hard-code the event type, so a receive row could carry `role='receive', event_type='put'`.
   Then `SUM(duration) WHERE role='put'` and the same query on `event_type` disagree, and
   there is nothing to point at. Every put and receive row must carry an event_type equal
   to its role, and no other row may claim one -- which also catches a put row typed 'pick'.
   Two exemptions, and both are declared rather than defensive. Pick rows are exempt because
   picking has its own vocabulary (task_start / pick / done / cut) and is the one stream
   where the two legitimately differ. `repack` is exempt because it is the one `role =
   'receive'` event type that is DECLARED to differ: `metrics.work_events.repack_rows` says
   outright that a repack is receiving work by the receiving crew at the dock's own per-pack
   price, so `role` is `'receive'` and only the event type moves -- which is what lets "what
   did receiving cost" sum `role='receive'` and get unloads AND rework while "how much rework
   was there" filters `event_type='repack'`. `repack` joins 'put' and 'receive' as a word no
   foreign row may claim, so the exemption widens the vocabulary rather than weakening it.

6. **The unload price is ONE constant.** Every `role='receive'` row satisfies

       duration - qty * sku_scores.handle_var(sku)  ==  C

   for a single C per run, because `unload_cost` IS `per_item + intercept + qty * v_s`
   (`Inbound/unload.py`, `Warehouse/kernel/cost_model.per_pick`) and `v_s` is already in this
   very DB: `UnloadCost.from_putaway` takes its weight/volume coefficients from
   `PutawayCost.from_pick`, which carries the PICK coefficients through unchanged, and
   `Order.compute_labor_cost` computes the stored `handle_var` from the same function on the
   same inputs. So the whole chain picking -> put-away -> receiving is checkable from one
   file: no catalogue, no config record, no fit.

   What it catches is a coefficient entering receiving by the side door -- a dock built from
   its class defaults instead of the run's pick config (the 55x drift `UnloadCost`'s docstring
   was written against), a weight/volume transform that diverged, an intercept scale applied
   twice. Every one of those moves the residual per ROW while leaving the two surfaces checks
   1 and 2 compare in perfect agreement with each other.

   Repack rows are INCLUDED, not filtered: `_charge_repack` prices a rescue with
   `dock.unload_seconds` -- the same `unload_cost` -- so a repack satisfies the same C, and
   including them makes this the only thing in the repo that verifies that docstring's central
   claim, *"no new coefficient enters the model"*. It is also why check 5's exemption lands
   first: a check that FAILed every repack row would mask this one.

   C is fixed by SPREAD (`max(c) - min(c) <= _TOL`) rather than against a reference, because
   there is no second place the intercept is written down; the recompute re-associates the
   float, so it is not bit-exact (relative error ~1e-14 on a ~50 s duration). An EMPTY
   `sku_scores` makes the check inactive -- the same idiom a run with no receiving crew gets,
   and not a silent PASS on a vintage that cannot answer. A receive row whose SKU is absent
   from a NON-empty `sku_scores` is a separate failure (`every_received_sku_is_scored`), kept
   separate because it is the one clause here that can go red for a reason outside receiving.

   ON THE ARCHIVE, this check partitions the runs by ERA, and the partition is sharp rather
   than marginal. Every run from 2026-09-06 on passes at a spread of 1e-15 .. 1e-13 seconds;
   every run before the per-item charge break (`fc7a46a5`, 2026-09-05) fails by ~4,600 s,
   because that era's dock was built from `UnloadCost`'s CLASS DEFAULTS rather than from the
   run's pick config -- measured on a 2026-09-01 arm: 1.03 s charged to unload a unit whose
   `handle_var` alone is 1.79 s. That is not a false alarm and it is not a vintage the check
   should be quiet about: it is the "second set of magic numbers" `UnloadCost`'s docstring
   was written against, realized, and it means pre-break receiving labour is not denominated
   in the same model as the rest of the run. A red verdict there is the tool working. What
   would have made the check unsound is an inability to tell the two apart, which is the sin
   the missing contiguity check below is a monument to -- and there is no ambiguity here:
   1e-15 against 4.6e+03.

   The cross-leaf clause -- `C_store == C_ful`, the site dock's sharpest falsifier -- needs two
   leaves and is not here. Note what the archive already says about it: the two channels run
   DIFFERENT pick configs (store intercept 15, fulfillment 10), so today's per-leaf docks price
   at C = 7.6 s and C = 5.1 s respectively. Equality is therefore a claim about a SITE dock
   having one price list, not something a coupled run would satisfy for free.

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


#: Every declared-shape column this tool touches, and HOW (phase-2 of the staged
#: semantics gate).  A PURE LITERAL: Tests/architecture/test_column_semantics.py
#: AST-reads it and validates against Schema/semantics.py without importing this
#: module, so declaring costs no dependency.
SEMANTIC_USES = {'sim_db': {
    'work_events.duration': 'sum', 'work_events.role': 'read',
    'work_events.actor_uid': 'read', 'work_events.t_abs': 'read',
    'work_events.batch_id': 'read', 'work_events.mode': 'read',
    'work_events.seq': 'read', 'work_events.event_type': 'read',
    'work_events.run_id': 'read',
    'work_events.sku': 'read', 'work_events.qty': 'read',
    'batch_stats.recv_unloaded': 'sum', 'batch_stats.recv_seconds': 'sum',
    'batch_stats.recv_cut': 'read', 'batch_stats.recv_depth': 'read',
    'batch_stats.run_id': 'read',
    # Check 6's two reads. `handle_var` is tagged SCORE, so a 'sum' here would be REFUSED
    # by the gate -- correctly: this check never sums it, it re-prices one row at a time.
    'sku_scores.sku': 'read', 'sku_scores.handle_var': 'read',
    'sku_scores.run_id': 'read',
}}


def _open_ro(path: str):
    """Read-only AND immutable: a plain `mode=ro` open still mints -wal/-shm sidecars beside
    an archived DB, and cannot remove them afterwards."""
    return sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)


def _has(con, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _unload_price(con, db_path: str, where: str, args: dict) -> dict:
    """Check 6's state: re-price every receive row from the run's OWN per-SKU handle term.

    `duration - qty * handle_var` must collapse to `per_item + intercept` -- one constant per
    run, carrying no SKU and no quantity. See the module docstring for why that identity holds
    and what a spread would mean.

    PER RUN, never across runs: `per_item` and `intercept` come from the run's pick config, so
    two runs in one file may legitimately price differently. A per-arm DB holds one run anyway
    (and since the fresh-run branch refuses to `create_run` over a db that already holds one,
    a second is legacy or additive), but the loop is what makes `run_id=None` honest.

    Reads `sku_scores` through the caller's IMMUTABLE connection rather than through
    `load_sku_scores`, which opens a plain `mode=ro`: that mints `-wal`/`-shm` beside an
    archived DB and cannot remove them (see `_open_ro`), and archived runs are exactly this
    tool's subject. The receive rows come through `load_receive_events`, whose own open is
    immutable -- and whose `receive_event_frame` query serves the columns this needs, already
    filtered to `role='receive'` (so repacks arrive included, which is the point). It and
    `load_receive_events` had ZERO callers since they were written; this is the caller they
    were written for. A second inline query here would be a second thing to keep in step with
    the DDL.
    """
    from Optimization.persistence.Picking_Data import load_receive_events

    state = {'unload_constants': {}, 'unload_spread': 0.0,
             'unload_unscored': 0, 'unload_unpriceable': 0, 'unload_note': None}
    if not _has(con, 'sku_scores'):
        state['unload_note'] = 'this vintage has no sku_scores; the unload price is unpriceable'
        return state

    run_ids = [r[0] for r in con.execute(
        f"SELECT DISTINCT run_id FROM work_events WHERE role = 'receive'{where} "
        f'ORDER BY run_id', args)]
    for rid in run_ids:
        # NULL handle_var is `not recorded by this vintage` (sim_semantics), which is absent
        # for this check's purposes -- the row simply cannot be re-priced from it.
        scores = {int(s): float(h) for s, h in con.execute(
            'SELECT sku, handle_var FROM sku_scores WHERE run_id = ? '
            'AND handle_var IS NOT NULL', (rid,))}
        if not scores:
            state['unload_note'] = (
                f'run {rid} recorded no usable sku_scores; the unload price cannot be '
                f'recomputed on this run')
            continue
        rows = load_receive_events(db_path, rid)
        if not rows:
            # There ARE receive rows (rid came from them), so an empty frame means the
            # vintage cannot serve the query -- inactive, which is not the same as clean.
            state['unload_note'] = (
                f'run {rid} has receive rows but this vintage cannot serve '
                f'receive_event_frame; check 6 did not run')
            continue
        cs = []
        for r in rows:
            dur, qty, sku = r['duration'], r['qty'], r['sku']
            if dur is None or qty is None or sku is None:
                # Reported, not failed: a NULL duration already shortens check 1's sum
                # against `recv_seconds`, and `recv_rows` refuses a non-positive qty at the
                # writer. Failing here as well would give one defect two red checks.
                state['unload_unpriceable'] += 1
                continue
            hv = scores.get(int(sku))
            if hv is None:
                state['unload_unscored'] += 1
                continue
            cs.append(float(dur) - float(qty) * hv)
        if not cs:
            continue
        state['unload_constants'][rid] = min(cs)
        state['unload_spread'] = max(state['unload_spread'], max(cs) - min(cs))
    return state


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
            # NOT SUM(recv_cut). `cut` is a LEVEL -- it equals `recv_depth` whenever a
            # whistle is in force, so summing counts a waiting unit once per batch it waits.
            # This reported 619,418 against a dock that never exceeded 6,162 on a 200-batch
            # run: 101x, as the headline number. What IS additive is how OFTEN the boundary
            # bit -- the count of batches with a non-zero cut.
            'COALESCE(SUM(recv_cut > 0), 0), COALESCE(MAX(recv_depth), 0) FROM batch_stats '
            + ('WHERE 1=1' + where if where else 'WHERE 1=1'), args).fetchone()

        out.update(seconds_events=secs_ev, seconds_batch_stats=secs_bs,
                   qty_events=n_ev, qty_batch_stats=unloaded,
                   cut_batches=cut, dock_depth_max=depth,
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
        # NO CONTIGUITY CHECK, and its absence is deliberate.
        #
        # There was one -- `sorted(seen) == range(len(seen))` -- meant to catch a crew
        # allocated from a hand-picked offset rather than the running cursor. It cannot: an
        # ALLOCATED-BUT-IDLE actor leaves exactly the same gap. Measured on a 200-batch run
        # whose rosters were provably correct (pick 0..24, store_cart 25, store_pallet 26,
        # fulfillment 27, receive 28), the observed uids were {0..24, 26, 28} because two of
        # the three put queues admitted nothing in 200 batches -- so the check failed a
        # healthy run, and would have passed the misallocation it was written for whenever
        # that crew happened to be idle. A check that cannot distinguish its failure from a
        # normal state is worse than none: it trains a reader to ignore a red verdict.
        #
        # DISJOINTNESS is the sound half and is what actually guards the collision: two crews
        # sharing a uid is the defect that merges two people in every per-actor rollup, and
        # it is visible here regardless of who was idle. `uid_blocks` is still reported so a
        # reader can see the layout and judge it against the roster themselves.

        # ── 4: the merge key is actually a key ────────────────────────────────────
        dupes = con.execute(
            f'SELECT COUNT(*) FROM (SELECT 1 FROM work_events WHERE 1=1{where} '
            f'GROUP BY t_abs, batch_id, role, mode, actor_uid, seq HAVING COUNT(*) > 1)',
            args).fetchone()[0]
        out['duplicate_merge_keys'] = dupes
        out['checks']['merge_key_is_total'] = (dupes == 0)

        # ── 5: role and event_type say the same thing ─────────────────────────────
        # Every PUT and RECEIVE row must carry an event_type equal to its role. Stated that
        # way rather than as `(role='receive') != (event_type='receive')`, which was the
        # first form: that catches a receive row typed as something else and a foreign row
        # typed 'receive', but sails past a put row typed 'pick'. Pick rows are excluded
        # because picking has a vocabulary of its own -- task_start, pick, done, cut -- and
        # is the one stream where role and event_type legitimately differ.
        #
        # `repack` is the SECOND exemption, and it is declared, not defensive:
        # `metrics.work_events.repack_rows` states that a repack IS receiving work by the
        # receiving crew at the dock's own per-pack price, so `role` comes from the worker
        # and is 'receive' while only the event type moves -- deliberately, so that "what did
        # receiving cost" sums role='receive' and gets unloads AND rework while "how much
        # rework was there" filters event_type='repack'. The clause below said the opposite,
        # so every rework row read as a mismatch and the arm read FAIL. It never fired only
        # because `f_repack` is `assumed` 0.0 and ADR-0003's own-bin rung engages only once
        # the free index is dry, which a warehouse sized to its declared levels never reaches
        # -- so this would have gone red on the FIRST honest run of the feature, by which
        # point a reader has been trained that check 5 is sound.
        #
        # The exemption is by (role, type) PAIR, not by type: a PUT row typed 'repack' is
        # still a mismatch, and 'repack' joins 'put'/'receive' as a word no foreign row may
        # claim. Weakening the whole clause to `(role='receive') != (event_type='receive')`
        # would have exempted repacks too, and is rejected for the reason above.
        mism = con.execute(
            f"SELECT COUNT(*) FROM work_events WHERE 1=1{where} AND ("
            f"  (role = 'put' AND event_type <> 'put')"
            f"  OR (role = 'receive' AND event_type NOT IN ('receive','repack'))"
            f"  OR (role NOT IN ('put','receive')"
            f"      AND event_type IN ('put','receive','repack')))",
            args).fetchone()[0]
        out['role_event_type_mismatches'] = mism
        out['checks']['role_matches_event_type'] = (mism == 0)

        # ── 6: the unload price is one constant ───────────────────────────────────
        out.update(_unload_price(con, db_path, where, args))
        # INACTIVE rather than PASS when nothing could be priced: an empty `sku_scores`, a
        # vintage that cannot serve the frame, or a run with no receiving at all. The key is
        # ABSENT from `checks` in that case, which is the same idiom `active=False` uses --
        # a `True` would claim the identity was verified on a file that cannot answer.
        if out['unload_constants']:
            out['checks']['unload_price_is_constant'] = (out['unload_spread'] <= _TOL)
        # Kept SEPARATE from the spread, because it is the one clause here that can go red
        # for a reason outside receiving: a run that unloaded a SKU it never scored is itself
        # the defect, and the missing score is the symptom, not the cause.
        if out['unload_unscored']:
            out['checks']['every_received_sku_is_scored'] = False
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
                      f"cut_batches={r.get('cut_batches', 0):,} "
                      f"depth_max={r.get('dock_depth_max', 0):,}")
                print(f"        uid blocks: {r.get('uid_blocks')}")
                cs = r.get('unload_constants') or {}
                if cs:
                    # C itself, beside the uid blocks: it is `per_item + intercept` in
                    # seconds, so a reader can sanity-check it against the run's pick config
                    # without opening one -- and two arms of the same pair should agree.
                    print('        unload C: '
                          + ', '.join(f'run {rid}: {c:,.6f}s' for rid, c in sorted(cs.items()))
                          + f" (spread {r.get('unload_spread', 0.0):.3e}, tol {_TOL:.0e}"
                          + (f", {r['unload_unscored']:,} unscored"
                             if r.get('unload_unscored') else '')
                          + (f", {r['unload_unpriceable']:,} unpriceable"
                             if r.get('unload_unpriceable') else '') + ')')
                elif r.get('unload_note'):
                    # ASCII only in PRINTED text: this CLI runs on a cp1252 console, where a
                    # stray em dash kills the process mid-report (windows-console-is-cp1252).
                    print(f"        unload C: INACTIVE - {r['unload_note']}")
            if bad:
                print(f'        FAILED: {", ".join(bad)}')

    print(f'receiving_report: {len(results)} arm(s), {len(active)} with receiving activity, '
          f'{len(failed)} FAILED')

    # Say what a spread MEANS -- ONCE, in the summary, not per arm: on the archive check 6
    # partitions by ERA rather than by defect (module docstring), so a run from before the
    # break reds every arm at once, and a reader who does not know that reads 16/16 red as a
    # broken tool. Sixteen copies of the explanation would read that way too.
    n_c = sum(1 for r in results if r['checks'].get('unload_price_is_constant') is False)
    if n_c:
        print(f'  ({n_c} arm(s) failed unload_price_is_constant: a spread means a coefficient '
              f'entered receiving from outside the run\'s own cost chain. On a run from '
              f'BEFORE the per-item charge break (fc7a46a5, 2026-09-05) that is expected -- '
              f'that era built the dock from UnloadCost class defaults rather than from the '
              f'pick config, and 1 unit cost 1.03 s against a 1.79 s/unit handle term. On a '
              f'current run it is a defect.)')
    if not active:
        # ASCII, not an em dash: this line printed as `receiving -- this run` with a
        # replacement glyph on the cp1252 console the tool actually runs on
        # (windows-console-is-cp1252), and the same character in a longer report kills the
        # process mid-write rather than merely mangling one word.
        print('  (no arm recorded any receiving -- this run had no receiving crew, which is '
              'a PASS, not a silence)')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
