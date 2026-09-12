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
   key makes re-inserted rows look correct, but it doubles this sum. The tolerance SCALES
   with the sum (`_tol_for`), because a flat one failed four archived arms on float
   re-association; read that function before changing either constant.

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

   The cross-leaf half of this check needs two leaves and lives in `reconcile_pair` below.
   `C_store == C_ful` -- which site-dock 15 called the site dock's sharpest falsifier and an
   earlier draft of this docstring promised -- is RETIRED and must not be written: the unload
   price is a statement about the MERCHANDISE, so the site dock holds a price LIST keyed by
   the unloaded unit's own regime (site-dock 27) and the equality is false BY CONSTRUCTION.
   What replaces it is the TWO-CONSTANT form, and it is strictly stronger; see
   `reconcile_pair`.

# ── the pair-scope checks, and why they are a sibling rather than five more arguments ──

`reconcile_pair` takes a COUPLED unit's two channel leaves and its site DB. Widening
`reconcile` with an optional peer was rejected (site-dock 15 section 5): it puts a scope
discriminator inside a function whose contract is "one DB, six checks", and every per-leaf
check would grow an `if peer is not None` branch that no run in the archive ever takes.

On an UNCOUPLED run there is no pair, so there is no verdict — `main` reports
`0 coupled pair(s)` rather than printing something green about a run that has no site. Every
published run is inbound-off and uncoupled, so that is the common case rather than the
corner. An ABSENT SITE DB on a coupled pair is a FAIL and not a MISSING: `MISSING` means
"this arm was not run", and a pair that produced two leaf DBs and no site DB is a run that
LOST its site scope.

  A. **No uid carries a site role in one leaf and a per-channel role in the other.** A uid
     is not an identity; `(role, uid)` is. Site-dock 04 preserves `actor_uid == picker_id`,
     so store picker 3 and fulfillment picker 3 are DIFFERENT people, while the put pool's
     block starts at `max(k_pickers)` over both channels precisely so a putter's uid means
     the SAME person in both DBs. The defect this catches is a leaf that builds its own site
     crew off its OWN pick cursor: fulfillment's putter lands at `k_ful`, inside store's
     picker block, and every per-actor rollup silently merges a putter with a picker.
     Nothing else in the repo can see it.

  B. **Every site-role uid lies at or above both leaves' pick blocks.** A FLOOR over
     `max(max pick uid) + 1` across the pair, and never set equality: an idle putter writes
     no rows in one leaf, and the smaller leaf is left a deliberate uid GAP (site-dock 19),
     so a check that assumed a dense range would be wrong by construction. That is the same
     trap the retired contiguity check below is a monument to.

  C. **The site total closes against the two leaves' rows, PER BATCH.** The sum over both
     leaves of `work_events` receive seconds equals the site dock's own
     `site_receiving.recv_seconds`. It is a real check rather than a second tautology because
     the two accumulators run on DIFFERENT CODE: the dock's total accrues at the charge site
     (`Dock.charge`), the leaves' rows carry per-row durations stamped from drained records.

     WHAT IT UNIQUELY COVERS IS NARROWER THAN THAT MAKES IT SOUND, and the narrowing is the
     honest claim. `SiteReceiving._partition` already ASSERTS that the leaves' own
     `receiving_seconds` sum to the dock's, and RAISES — so a routing or decomposition defect
     kills the run before a row is written and never reaches this check. What is left for
     this one is everything that goes wrong AFTER the partition: a row that never reached the
     DB, a flush that never fired, a checkpoint buffer that was not cleared. That is the one
     class no per-leaf check can see either — a final flush that never fired takes BOTH of a
     leaf's surfaces with it, so check 1 passes over exactly that defect while this one
     fails.

     Per batch, never a run total: a run total says the site lost 400 seconds and nothing
     about where, and the drain IS a batch boundary. The counts close too, and they filter
     `event_type='receive'`: a REPACK is receiving work priced at the dock and charged into
     `recv_seconds`, but no `unloaded` counter counts it, so the seconds closure includes
     repacks and the count closure cannot.

  D. **The two constants are per regime** — check 6's cross-leaf half, in the two-constant
     form site-dock 27 chose over the retired equality. Each leaf is regime-PURE (the
     coordinator partitions the dock's records by SKU before the driver stamps anything, so
     one DB never holds both regimes' rows), so `C_store` is measured over the store leaf's
     rows and `C_ful` over the fulfillment leaf's, each against its own regime's entry in the
     dock's price list. Three clauses, because the defect has three shapes and only the
     per-leaf spread is visible from one DB:

       * each leaf's own residuals collapse to ONE constant (check 6, restated at pair scope
         so the pair verdict does not depend on a reader having run the per-leaf one);
       * NO ROW of either leaf prices at the OTHER regime's constant — the exact second
         equality, which NAMES a contaminated row rather than only reporting a spread;
       * and if the two leaves price their PICKING differently, they must price their
         UNLOADING differently too. `sku_scores.labor_cost - handle_var` is
         `pick_intercept + pick_per_item`, one constant per run, recoverable from the same
         file; `C` is a positive linear map of that same pair of coefficients through
         site-wide scales. So two channels whose pick constants differ cannot charge the
         same unload price unless the two terms exactly cancel. THAT is the clause that sees
         a WHOLE leaf charged at the other channel's rate — the shape the per-leaf spread is
         blind to, because a uniformly mispriced leaf has a spread of zero.

     Both cross-leaf clauses go INACTIVE (reported, never a silent pass) when the pair's two
     constants coincide: two channels MAY run the same pick config, and from two sim DBs
     "the same config" and "one regime resolved to the other" are the same reading.

# ── usage ─────────────────────────────────────────────────────────────────────────

    python Diagnostics/receiving_report.py <run_root_or_name>     # every arm, PASS/FAIL
    python Diagnostics/receiving_report.py <run_root> --verbose   # per-arm numbers

Run roots may be bare names (resolved under COMPARISON_OUTPUT_DIR) or absolute paths. Arms
are enumerated through the run's own contract (`runschema.resolver_for`) and never by joining
path strings: the store CONFIG and the store CHANNEL are both named `store`, so
`<cell>/<pair>/store/store/` is a real path, and two levels are conditional. The pair pass
runs on top of the same walk when the run layout carries the `coupled` marker.
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a script ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: Seconds. The FLOOR of every seconds tolerance here, and on its own the whole tolerance
#: for anything that is not a growing sum (check 6's spread, which is a spread of constants
#: and does not accumulate: measured at 4.4e-15 over 505,177-row arms).
_TOL = 1e-6

#: The RELATIVE companion, applied only where a SUM is compared against a SUM. See
#: `_tol_for` — this is the decision the map's fog carried, and it is taken here.
_REL_TOL = 1e-9

#: Decimal places `_dominant_constant` buckets residuals at — DERIVED from `_TOL` so the two
#: cannot drift. A bucket wider than the tolerance would merge two genuinely distinct rates;
#: narrower, and float noise would scatter one rate across several buckets.
_BUCKET_DP = round(-math.log10(_TOL))


def _tol_for(*magnitudes) -> float:
    """The seconds tolerance for comparing two accumulations of size ~`magnitudes`.

    # ── why an absolute tolerance alone was wrong, and why a relative one alone is too ──

    `_TOL` was 1e-6 SECONDS flat, compared against sums that GROW WITH THE ROW COUNT. On a
    505,177-row arm check 1's two surfaces accumulate 1.3e-6 s apart over 4,177,040.9 s —
    a relative error of 3e-13, which is float re-association and nothing else — and the tool
    reports it as a FAIL. It does so on four arms of the archive today, and it gets sharper
    under coupling, where a SITE total is the sum of two leaves' and carries both leaves'
    accumulation error.

    The objection that kept this open is real and is the right objection: a purely RELATIVE
    tolerance hides a small real discrepancy on a large arm, which is the failure check 1
    exists for. What settles it is that the discrepancy check 1 hunts has a PHYSICAL LOWER
    BOUND. Every defect it was written for — a dropped `we.extend`, an unrun `recv_seconds`
    assignment, an uncleared checkpoint buffer — moves the sum by at least ONE UNLOAD, and
    the smallest unload the archive actually prices is 5.1 s (the fulfillment constant;
    store's is 7.6 s). So the question is only whether the tolerance can be kept far below
    one unload while sitting far above float noise, and at this scale it can:

        arm                 sum (s)      1e-9 x sum    observed float noise   one unload
        505,177 rows      4,177,040.9      4.2e-3 s          1.3e-6 s          5.1-7.6 s
        a 12-batch pair       62,278.9      6.2e-5 s          ~1e-11 s          5.1-7.6 s

    4.2e-3 s is under a THOUSANDTH of the smallest thing that can really go missing, and
    three orders above the observed noise. The absolute floor is kept so that a tiny run —
    or a comparison against zero — is not handed a tolerance of zero.

    THE UPPER MARGIN IS EMPIRICAL, NOT A BOUND, and that is worth knowing before this is
    reused at a larger scale. Worst-case float summation error is `N * eps * |sum|`, which
    at 505,177 rows is ~1.1e-10 relative — only ~9x under this floor rather than the ~3e4x
    the OBSERVED 3e-13 suggests. An arm an order of magnitude larger (~5M rows) would put
    the worst case above `_REL_TOL` and the false FAIL would return. At that point the
    relative term wants scaling by the row count, not raising.

    IT ALSO CHANGES VERDICTS ON THE ARCHIVE, dated here so a later reader is not confused by
    a record that no longer reproduces: the four arms recorded as failing check 1 on
    1.3e-6 s of re-association PASS from 2026-09-12 (site-dock 25). Nothing about those runs
    moved; the tolerance did.

    Applied ONLY to sums. A spread (check 6) does not accumulate: it is a max minus a min
    over per-row residuals, so its error is bounded by one row's rounding however many rows
    there are, and giving it a row-count-dependent tolerance would loosen it for no reason.
    """
    return max(_TOL, _REL_TOL * max((abs(float(m)) for m in magnitudes), default=0.0))


#: THE SITE ROLES, and this tuple is the whole "roster artifact" site-dock 15 decided not to
#: build. Which roles a SITE crew does is a constant in the scope the pair checks run in: a
#: pair check runs only on a COUPLED run, where put-away is one pool over both channels
#: (site-dock 04/19) and receiving is one crew on one dock (01/21/24). `pick` is the
#: per-channel role, deliberately and permanently — pickers really are per-channel crews
#: (`staffing.py`), and coupling the pick floor is off this map's destination entirely.
#:
#: THE PARTITION IS ASSERTED TOTAL rather than assumed (`roles_are_classified`): a fourth
#: role added to `Warehouse/operations/roles.py` would otherwise be silently unclassified and
#: both clauses below would pass over every row of it.
_SITE_ROLES = ('put', 'receive')
_CHANNEL_ROLES = ('pick',)


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
    # ── the PAIR-scope reads (reconcile_pair) ────────────────────────────────────
    # ONE FAMILY, not two, and site-dock 15 section 6 expected two. The site DB IS a
    # sim DB -- the contract gives `site_inbound_db` the `sim_db` family so every
    # analysis broker binds it with no new loader (site-dock 24) -- so there is no
    # second family key to declare and the ordering constraint 15 recorded is
    # discharged rather than satisfied. `semantics_for` raising on an unregistered
    # family is exactly why that mattered, and it cannot bite here.
    'simulation_runs.channel': 'read', 'simulation_runs.run_id': 'read',
    'simulation_runs.pick_intercept': 'read',
    # `labor_cost - handle_var` is `pick_intercept + pick_per_item`: the run's own pick
    # constant, which clause D compares the two leaves' unload constants against. Read,
    # never summed -- like `handle_var` it is tagged SCORE.
    'sku_scores.labor_cost': 'read',
    # The site dock's own per-batch totals, in the site DB alone. `recv_seconds` and
    # `recv_unloaded` are SUMMED (they are FLOWs, per batch, and the closure is over the
    # run); `recv_cut` and `recv_depth` are LEVELs and are only ever READ -- summing
    # either is the 101x headline this file's own check-2 comment records.
    'site_receiving.batch': 'read', 'site_receiving.run_id': 'read',
    'site_receiving.recv_seconds': 'sum', 'site_receiving.recv_unloaded': 'sum',
    'site_receiving.recv_cut': 'read', 'site_receiving.recv_depth': 'read',
}}


def _open_ro(path: str):
    """Read-only AND immutable: a plain `mode=ro` open still mints -wal/-shm sidecars beside
    an archived DB, and cannot remove them afterwards."""
    return sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)


def _finite(v) -> bool:
    """Whether a seconds value is a real number a tolerance can be compared against.

    REACHABLE, and via INFINITY rather than NaN. SQLite stores a Python NaN as NULL, so a
    NaN never comes back out of these queries -- but an infinity round-trips, and `SUM` of a
    column holding one is `inf`. That breaks the comparison two ways at once: `_tol_for(inf)`
    is itself `inf`, so `abs(x - inf) > inf` is False for any finite other side; and
    `abs(inf - inf)` is NaN, which compares False against everything. A check that only
    compared would report a clean PASS about a value that is not a number.

    Named rather than inlined because both surfaces that sum seconds need it.
    """
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _fmt_s(v) -> str:
    """A seconds constant for the report, or `-` when it could not be measured.

    ASCII only, like every other printed string here: this CLI runs on a cp1252 console
    where a stray em dash kills the process mid-report (`windows-console-is-cp1252`).
    """
    return '-' if v is None else f'{v:,.6f}s'


def _has(con, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _unload_price(con, db_path: str, where: str, args: dict,
                  keep_residuals: bool = False) -> dict:
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

    `keep_residuals` additionally returns EVERY row's `c` under `unload_residuals`, which the
    PAIR-scope clause needs and `reconcile` deliberately does not: the pair check asks which
    rows price at the OTHER channel's constant, and a min-and-spread summary cannot answer
    that. Off by default, because a 505,177-row arm would otherwise carry half a million
    floats out of a function whose caller wants two numbers.
    """
    from Optimization.persistence.Picking_Data import load_receive_events

    state = {'unload_constants': {}, 'unload_spread': 0.0,
             'unload_unscored': 0, 'unload_unpriceable': 0, 'unload_note': None}
    if keep_residuals:
        state['unload_residuals'] = {}
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
        if keep_residuals:
            state['unload_residuals'][rid] = cs
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
        # SCALED, because both sides are SUMS: see `_tol_for`. A flat 1e-6 s failed four
        # archived arms on 1.3e-6 s of float re-association over 4.18M s.
        # A NON-FINITE side is a FAIL, not a pass. An infinite `SUM(duration)` makes
        # `_tol_for` itself infinite, so every comparison against it is False and the
        # check reports agreement about a number that is not one (see `_finite`).
        out['seconds_tol'] = _tol_for(secs_ev, secs_bs)
        out['checks']['seconds_agree'] = (
            _finite(secs_ev) and _finite(secs_bs)
            and abs(secs_ev - secs_bs) <= out['seconds_tol'])
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


# ══ the pair scope: one coupled unit, two channel leaves, one site dock ══════════════

def _leaf_state(db_path: str) -> dict:
    """Everything the pair clauses need out of ONE channel leaf's sim DB, in one open.

    Gathered per leaf rather than per clause so the file is opened once (these are archived
    WAL-less files and `_open_ro`'s immutable flag is what keeps a read from minting sidecars
    it cannot remove -- memory `wal-sidecars-come-from-readers`).

    THE CHANNEL COMES OFF `simulation_runs.channel`, never off the directory the file sits
    in. A leaf's config directory and its channel directory can carry the same name (the
    store config and the store channel are both `store`), and site-dock 24 established that
    the regime a row belongs to is resolvable from the sim DB ALONE precisely because this
    column names it -- which is what made check 6's two-constant form buildable with no new
    column and no `--sync`.

    `pick_constant` is `labor_cost - handle_var` off `sku_scores`, which is
    `pick_intercept + pick_per_item` (`Order.compute_labor_cost` computes `labor_cost` as
    `per_pick(1.0, intercept, handle_var, 1, per_item)`). One constant per run, and its
    SPREAD is reported so a caller can see whether it really is one before leaning on it.
    """
    st = {'db': os.path.basename(db_path), 'path': db_path, 'channel': None,
          'roles': {}, 'by_batch': {}, 'seconds': 0.0, 'unloads': 0, 'receive_rows': 0,
          'batch_stats_unloaded': 0, 'batch_stats_cut_batches': 0,
          'pick_constant': None, 'pick_constant_spread': 0.0, 'pick_intercept': None,
          'unload_constants': {}, 'unload_residuals': {}, 'unload_spread': 0.0,
          'note': None}
    if not os.path.isfile(db_path):
        st['note'] = 'MISSING'
        return st
    con = _open_ro(db_path)
    try:
        if not _has(con, 'work_events') or not _has(con, 'simulation_runs'):
            st['note'] = 'this vintage predates work_events'
            return st
        # ONE RUN PER LEAF FILE, refused rather than summed. `reconcile` deliberately
        # spans every run in a file (`run_id=None`), but the pair claims are RUN-scoped: the
        # closure compares this leaf's rows against one site day's counters, and a file that
        # acquired a second run would put an ABANDONED run's rows on one side of it.
        # `_plan_strategy_start` refuses to `create_run` over a db that already holds one,
        # so this is unreachable through the writer -- and `find_run`'s `ORDER BY run_id
        # LIMIT 1` is what makes a file that got there anyway answer from the wrong run.
        n_runs = con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]
        if n_runs != 1:
            st['note'] = (f'the file holds {n_runs} run(s); a coupled leaf holds exactly '
                          f'one, and the pair claims are run-scoped')
            return st
        # ONE run, so ONE channel -- read, not aggregated. NULL is a legacy store-only run,
        # which predates channels entirely and cannot be a leaf of a coupled pair.
        st['channel'] = con.execute('SELECT channel FROM simulation_runs').fetchone()[0]
        if not st['channel']:
            st['note'] = ('the file names no channel; a coupled leaf names exactly one, '
                          'and a NULL is a legacy store-only run')
            return st
        ints = sorted({round(float(v), 12) for (v,) in con.execute(
            'SELECT DISTINCT pick_intercept FROM simulation_runs') if v is not None})
        st['pick_intercept'] = ints[0] if len(ints) == 1 else None

        # (role, uid) -> the identity. Gathered as role SETS per uid, because clause A asks
        # what ONE uid does in each leaf and check 3's per-leaf disjointness already owns
        # the within-leaf question.
        for role, uid in con.execute('SELECT DISTINCT role, actor_uid FROM work_events'):
            st['roles'].setdefault(int(uid), set()).add(str(role))

        # The receiving stream, PER BATCH -- the grain the site total is written at, so the
        # closure can localise. `event_type='receive'` separates unloads from repacks: both
        # are receiving labour and only the unloads are counted by an `unloaded` counter.
        for batch, secs, unloads, rows in con.execute(
                "SELECT batch_id, COALESCE(SUM(duration), 0.0), "
                "COALESCE(SUM(event_type = 'receive'), 0), COUNT(*) "
                "FROM work_events WHERE role = 'receive' GROUP BY batch_id"):
            st['by_batch'][int(batch)] = (float(secs), int(unloads))
            st['seconds'] += float(secs)
            st['unloads'] += int(unloads)
            st['receive_rows'] += int(rows)

        # THE LEAF'S OTHER RECEIVING SURFACE, read for ONE purpose: deciding whether this
        # pair received AT ALL. `receive_rows` alone is too narrow a witness, and the gap is
        # exactly the total-loss case the pair checks exist for -- a coordinator whose drain
        # reached NO artifact writes no `work_events` rows, so a pair with no site DB either
        # would read as the inbound-off pole and pass. `reconcile` already defines activity
        # as `n_ev or unloaded or cut`; this is the same definition, one scope up.
        if _has(con, 'batch_stats'):
            unl, cut = con.execute(
                'SELECT COALESCE(SUM(recv_unloaded), 0), '
                'COALESCE(SUM(recv_cut > 0), 0) FROM batch_stats').fetchone()
            st['batch_stats_unloaded'] = int(unl)
            st['batch_stats_cut_batches'] = int(cut)

        if _has(con, 'sku_scores'):
            lo, hi = con.execute(
                'SELECT MIN(labor_cost - handle_var), MAX(labor_cost - handle_var) '
                'FROM sku_scores WHERE labor_cost IS NOT NULL '
                'AND handle_var IS NOT NULL').fetchone()
            if lo is not None:
                st['pick_constant'] = float(lo)
                st['pick_constant_spread'] = float(hi) - float(lo)
        st.update({k: v for k, v in
                   _unload_price(con, db_path, '', {}, keep_residuals=True).items()
                   if k in ('unload_constants', 'unload_residuals', 'unload_spread')})
    finally:
        con.close()
    return st


def _dominant_constant(residuals) -> float:
    """The rate a leaf MOSTLY charged, out of its re-priced residuals.

    NOT `min`, which is what `reconcile` reports per leaf and is right there: on a clean leaf
    the spread is ~1e-15 and every summary of the residuals is the same number. This one is
    used where the leaf may be PARTLY contaminated, and there `min` is actively wrong — a
    single store row charged at the fulfillment rate makes `min` return the FULFILLMENT
    constant, the two leaves then measure the same C, and the cross-regime clause goes
    inactive on exactly the defect it exists to name. (Found by the test, not by reading.)

    The mode over residuals rounded to `_BUCKET_DP` places — DERIVED from `_TOL`, so a change
    to the tolerance carries the bucket with it: every correctly-priced row lands in one
    bucket, so it is the rate the crew actually charged. The value RETURNED is a real
    residual from that bucket rather than the rounded key, so a comparison against `_TOL`
    is not spending half its budget on the rounding.

    A leaf split exactly 50/50 has no dominant rate and this picks one arbitrarily — which
    is the right answer anyway: the other half is then named as cross-priced, and the
    leaf's own spread has already gone red.
    """
    buckets: dict = {}
    for c in residuals:
        buckets.setdefault(round(c, _BUCKET_DP), []).append(c)
    return buckets[max(buckets, key=lambda k: len(buckets[k]))][0]


def _site_totals(site_db: str | None) -> tuple:
    """`(by_batch, note, answerable)` — the SITE dock's per-batch counters, or why not.

    `answerable` is False ONLY for a file that cannot hold the answer — a site DB written
    before `site_receiving` existed. That is the difference between "the site total is
    missing" (a defect: the rows were parked and never reached an artifact) and "this
    vintage never recorded one" (a fact about the file), and conflating them would make the
    tool red on every site DB site-dock 24's build wrote.

    The `site_db is None` and "does not exist" branches are NOT reachable from `main`, which
    only ever passes a path `rt.site_inbound_dbs` just found (a pair with no site DB never
    reaches `reconcile_pair` at all — `main` reports it separately). They are kept because
    `reconcile_pair` is callable on its own and a caller that hands it None should get the
    no-site-total answer rather than an exception.

    `by_batch` is `{batch: (seconds, unloaded)}`, straight off `site_receiving`.

    READ WITH AN INLINE QUERY, and that is a deviation from CLAUDE.md section 2 worth stating
    rather than leaving to be noticed. The rule is that SQL belongs in a named query beside
    the family with a per-vintage `dataset.override` where a shape moved. What moved here is
    not a shape: the whole TABLE is absent before this build, so there is no older column
    layout for an override to describe -- the negotiation is "does it exist", which is the
    `_has` probe this file already uses for `sku_scores` and which every one of checks 1-5
    reads `work_events` and `batch_stats` through. A named query plus an override that says
    "on every earlier vintage, nothing" would be a second declaration of the same absence.
    Check 6 is the one clause here that goes through a loader, and only because
    `receive_event_frame` already existed and had been waiting for a caller.
    """
    if not site_db:
        return {}, 'the pair names no site DB', True
    if not os.path.isfile(site_db):
        return {}, f'{os.path.basename(site_db)} does not exist', True
    con = _open_ro(site_db)
    try:
        if not _has(con, 'site_receiving'):
            # A site DB written before this table existed (site-dock 24's build). INACTIVE
            # and said so, never a silent pass: the closure genuinely cannot be evaluated.
            return {}, (f'{os.path.basename(site_db)} predates `site_receiving`; the site '
                        f'total was not recorded and the closure did not run'), False
        # ONE RUN, refused rather than merged -- the leaf side's rule, on the other side
        # of the same comparison. The PK is `(run_id, batch)`, so reading row by row is
        # LAST-WINS across runs and summing would ADD an abandoned run's counters to a live
        # one; neither is a number. `_SiteDock.finish` refuses a file that already holds a
        # run (and the torn-pair reconciler removes it), so this is unreachable through the
        # writer -- which is not a reason to read it as though it could not happen.
        n_runs = con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]
        if n_runs != 1:
            return {}, (f'{os.path.basename(site_db)} holds {n_runs} run(s); a site DB '
                        f'holds exactly one, and `find_run` answers from the OLDEST'), False
        rows = {int(b): (float(s), int(u)) for b, u, s in con.execute(
            'SELECT batch, SUM(recv_unloaded), SUM(recv_seconds) '
            'FROM site_receiving GROUP BY batch')}
    finally:
        con.close()
    return rows, None, True


def reconcile_pair(leaf_dbs, site_db: str | None) -> dict:
    """Cross-check ONE coupled arm pair: two channel leaves against the site's own dock.

    `leaf_dbs` are the leaves of one arm pair -- one DB per channel, each naming its channel
    in `simulation_runs.channel`. `site_db` is that pair's site inbound DB (the contract's
    `site_inbound_db`), or None when the pair produced none. Returns a dict; never raises on content, exactly as
    `reconcile` does.

    Four clauses, A-D in the module docstring, and every one of them is UNSTATEABLE from one
    DB. What is NOT here is as deliberate: checks 1 and 2 stay PER LEAF and unedited
    (site-dock 15 section 2), because moving them to site scope discards the per-channel
    attribution ADR-0005 goes to some length to keep -- and because both of their surfaces
    now delegate to one coordinator-held dock, so a leaf handed the whole drain would agree
    with itself and PASS while owning the other channel's entire receiving labour. Clause C
    is what sees that.

    ON THE ABSENT SITE DB, and this SHARPENS site-dock 15 section 5 rather than restating it.
    15 said an absent site DB on a coupled pair is a FAIL, full stop. It cannot be: site-dock
    06 couples every cell INCLUDING its inbound-OFF pole, and site-dock 24's
    `_build_site_dock` returns None there -- no trailer pipeline, no dock, no site DB, and
    that is a real configuration rather than a degenerate one. So the rule is conditioned on
    the evidence: a pair whose leaves recorded RECEIVING and has no site DB is a run that
    lost its site scope and FAILs; a pair whose leaves recorded none is the inbound-off pole
    and is a PASS with `active=False`. That keeps 15's rule reachable (it fires on exactly
    the defect it was written for -- site-dock 21's parked rows never reaching an artifact)
    without failing half the campaign.
    """
    out: dict = {'site_db': os.path.basename(site_db) if site_db else None,
                 'active': False, 'checks': {}, 'leaves': {}, 'verdict': 'PASS'}
    states = [_leaf_state(p) for p in leaf_dbs]
    notes = [s['note'] for s in states if s['note']]
    if notes:
        # MISSING an arm is not the same as a broken pair: a leaf DB that was never written
        # is an arm that was not run, which is the idiom `reconcile` already uses.
        return {**out, 'verdict': 'MISSING' if 'MISSING' in notes else 'SKIP',
                'note': '; '.join(dict.fromkeys(notes))}
    chans = [s['channel'] for s in states]
    if len(set(chans)) != len(chans) or len(chans) < 2:
        return {**out, 'verdict': 'SKIP',
                'note': f'a coupled pair is two leaves of DIFFERENT channels; got {chans!r}'}
    # The DOMINANT constant, not `reconcile`'s `min` — see `_dominant_constant`: on a
    # partly-contaminated leaf `min` returns the OTHER channel's rate and silences the very
    # clause that names it. Identical on every clean leaf, where the spread is ~1e-15.
    for s in states:
        rows = [c for cs in s['unload_residuals'].values() for c in cs]
        s['constant'] = _dominant_constant(rows) if rows else None
    out['leaves'] = {s['channel']: {
        'db': s['db'], 'receive_rows': s['receive_rows'], 'seconds': s['seconds'],
        'unloads': s['unloads'], 'unload_constant': s['constant'],
        'unload_spread': s['unload_spread'], 'pick_constant': s['pick_constant'],
        'pick_constant_spread': s['pick_constant_spread'],
        'pick_intercept': s['pick_intercept']} for s in states}
    # DID THIS PAIR RECEIVE? -- the witness for the absent-site-DB rule, and it has to be
    # `reconcile`'s own definition of activity rather than the row count alone. A drain that
    # reached NO artifact writes no rows, so a row-only witness reads the total loss as the
    # inbound-off pole and passes it, which is the defect this rule exists for in its most
    # complete form.
    received = sum(s['receive_rows'] + s['batch_stats_unloaded']
                   + s['batch_stats_cut_batches'] for s in states)

    # ── A + B: the site crews' uids, across the pair ──────────────────────────────
    seen_roles = {r for s in states for rs in s['roles'].values() for r in rs}
    unknown = sorted(seen_roles - set(_SITE_ROLES) - set(_CHANNEL_ROLES))
    out['unclassified_roles'] = unknown
    out['checks']['roles_are_classified'] = not unknown
    # Clause A. A uid that is a site crew member in one leaf and a picker in the other is
    # the defect site-dock 04 names: a leaf that built its put crew off its OWN pick cursor
    # puts fulfillment's putter at uid k_ful, inside store's picker block.
    crossed = []
    for uid in sorted({u for s in states for u in s['roles']}):
        here = [s['roles'].get(uid, set()) for s in states]
        site_in = {s['channel'] for s, r in zip(states, here) if r & set(_SITE_ROLES)}
        chan_in = {s['channel'] for s, r in zip(states, here) if r & set(_CHANNEL_ROLES)}
        if site_in and chan_in - site_in:
            crossed.append((uid, sorted(site_in), sorted(chan_in - site_in)))
    out['crossed_uids'] = crossed
    out['checks']['no_uid_is_site_and_channel'] = not crossed
    # Clause B. A FLOOR over both leaves' pick blocks, never set equality: an idle putter
    # writes no rows in one leaf, and the smaller leaf is left a deliberate uid GAP by the
    # pool's `first_uid = max(k_pickers)` (site-dock 19), so a dense-range assumption is
    # wrong by construction. Inactive when neither leaf recorded a pick row -- there is no
    # floor to be above, and inventing one would be a check with no subject.
    pick_uids = [u for s in states for u, r in s['roles'].items() if r & set(_CHANNEL_ROLES)]
    site_uids = sorted({u for s in states for u, r in s['roles'].items()
                        if r & set(_SITE_ROLES)})
    if pick_uids and site_uids:
        floor = max(pick_uids) + 1
        below = [u for u in site_uids if u < floor]
        out.update(site_uid_floor=floor, site_uids_below_floor=below)
        out['checks']['site_uids_above_both_pick_blocks'] = not below

    # ── C: the site total closes against the two leaves' rows, per batch ──────────
    site_by_batch, site_note, answerable = _site_totals(site_db)
    out['site_note'] = site_note
    out['site_batches'] = len(site_by_batch)
    leaf_by_batch: dict = {}
    for s in states:
        for b, (secs, unloads) in s['by_batch'].items():
            have = leaf_by_batch.get(b, (0.0, 0))
            leaf_by_batch[b] = (have[0] + secs, have[1] + unloads)
    out['leaf_receive_seconds'] = sum(v[0] for v in leaf_by_batch.values())
    out['site_receive_seconds'] = sum(v[0] for v in site_by_batch.values())
    out['active'] = bool(received or site_by_batch)
    if site_by_batch:
        bad_s, bad_n = [], []
        for b in sorted(set(leaf_by_batch) | set(site_by_batch)):
            ls, lu = leaf_by_batch.get(b, (0.0, 0))
            ss, su = site_by_batch.get(b, (0.0, 0))
            # PER BATCH, so a leak in batch 3 offset by a double count in batch 7 cannot
            # pass a run total -- and so the report names the batch rather than the run.
            #
            # A NON-FINITE side is a FAIL, not a pass -- the same hole as check 1's, and
            # reachable through an infinite duration rather than a NaN (see `_finite`).
            if not (_finite(ls) and _finite(ss)) or abs(ls - ss) > _tol_for(ls, ss):
                bad_s.append((b, ls, ss))
            if lu != su:
                bad_n.append((b, lu, su))
        out['site_seconds_mismatches'] = bad_s
        out['site_count_mismatches'] = bad_n
        out['checks']['site_seconds_close'] = not bad_s
        out['checks']['site_counts_close'] = not bad_n
    elif received and answerable:
        # 15 section 5's rule, conditioned on the evidence (see the docstring). The leaves
        # DID receive, so a site dock ran and its own totals are nowhere -- the rows were
        # parked and never reached an artifact. `answerable` keeps a site DB that PREDATES
        # the table out of it: that file never recorded a total, which is a fact about the
        # vintage rather than a defect in the run.
        out['checks']['site_totals_present'] = False

    # ── D: the two constants are per REGIME (check 6's cross-leaf half) ───────────
    priced = [s for s in states if s['constant'] is not None]
    if len(priced) == len(states) and len(states) == 2:
        out['checks']['unload_price_is_constant_per_leaf'] = all(
            s['unload_spread'] <= _TOL for s in states)
        a, b = states
        ca, cb = a['constant'], b['constant']
        out['unload_constant_separation'] = abs(ca - cb)
        # SEPARATED BY MORE THAN TWICE the tolerance, not once. At a separation of exactly
        # `_TOL < |ca - cb| <= 2 * _TOL` every CLEAN row of one leaf is also within `_TOL`
        # of the other leaf's constant, so the cross-price clause would fail a healthy pair.
        # Unreachable on real data (the two constants differ by ~2.5 s or are identical),
        # and stated as a bound rather than left to be reachable one day: the two branches
        # are mutually exclusive BY CONSTRUCTION this way.
        if abs(ca - cb) > 2 * _TOL:
            # THE SECOND EQUALITY. Every store row prices at C_store and every fulfillment
            # row at C_ful; a row that satisfies the OTHER leaf's constant was charged at
            # the other channel's rate. The per-leaf spread goes red on the same defect, but
            # only this NAMES it -- a spread of 2.5 s says nothing about what 2.5 s is.
            cross = {}
            for s, other in ((a, cb), (b, ca)):
                n = sum(1 for rows in s['unload_residuals'].values()
                        for c in rows if abs(c - other) <= _TOL)
                if n:
                    cross[s['channel']] = n
            out['rows_priced_at_the_other_regime'] = cross
            out['checks']['no_row_priced_at_the_other_regime'] = not cross
        else:
            out['site_price_note'] = (
                'the two leaves price identically, so the cross-regime clauses are '
                'INACTIVE: from two sim DBs, "both channels run the same pick config" and '
                '"one regime resolved to the other" are the same reading')
        # THE WHOLE-LEAF CLAUSE, and it is the only one that can see a leaf uniformly
        # charged at the other channel's rate (a uniformly mispriced leaf has a spread of
        # ZERO and a cross-price count of zero, because its own constant moved with it).
        # C is `A*pick_intercept + B*pick_per_item` with A, B site-wide and positive, and
        # `pick_constant` is `pick_intercept + pick_per_item` off the same file -- so two
        # channels whose pick constants differ cannot charge the same unload price unless
        # the two terms exactly cancel. When their recorded `pick_intercept` also agrees
        # that cancellation is impossible and the implication is EXACT; when it does not,
        # it needs a coincidence no defect produces, and `pick_intercept` is reported
        # beside C so a reader can tell which branch they are in.
        #
        # GATED ON EACH LEAF'S PICK CONSTANT REALLY BEING ONE. `pick_constant` is the MIN of
        # `labor_cost - handle_var` over that leaf's scored SKUs, and the clause leans on the
        # two minima differing. If a vintage ever made that difference SKU-dependent the two
        # minima could coincide while the constants genuinely differ, and the clause would
        # go quiet on exactly the leaf-wide contamination it is the only detector for. So
        # the spread is READ rather than merely reported: a leaf whose pick constant is not
        # a constant cannot support the inference, and the clause stays inactive and says so.
        ka, kb = a['pick_constant'], b['pick_constant']
        spread = max(a['pick_constant_spread'], b['pick_constant_spread'])
        if ka is not None and kb is not None and spread > _TOL:
            out['pick_price_note'] = (
                f'a leaf pick constant spreads by {spread:.3e} s over its own SKUs, so '
                f'`labor_cost - handle_var` is not one constant there and the whole-leaf '
                f'clause has no ground to stand on; it is INACTIVE')
        elif ka is not None and kb is not None and abs(ka - kb) > _TOL:
            out['pick_constant_separation'] = abs(ka - kb)
            out['checks']['the_two_regimes_price_differently'] = abs(ca - cb) > _TOL
    elif received:
        out['unload_note'] = ('at least one leaf could not be re-priced (no usable '
                              'sku_scores, or a vintage that cannot serve the frame); '
                              'the cross-leaf price clauses did not run')

    out['verdict'] = 'PASS' if all(out['checks'].values()) else 'FAIL'
    return out


def _sim_dbs(root: str):
    """Every arm's sim DB, through the run's own contract."""
    from Optimization.runschema import resolve_base_dir, resolver_for
    base = resolve_base_dir(root)
    rt = resolver_for(base)
    return base, rt, sorted(rt.glob('sim_db'))


def _split_arm_pair(rt, arm_pair: str, by_channel: dict) -> list:
    """The leaf DBs of one arm pair, through the contract's OWN inversion.

    `rt.arm_pair_halves` is the other half of `rt.arm_pair_of` and owns the whole rule --
    the positional read against the declared channel order, the every-split-position scan
    (an arm name may contain the joiner, which is why the arm pair is ONE capture in the
    declared template), and the refusal when no channel order is declared. This only turns
    the `{channel: arm_key}` it returns back into the paths the caller already holds.

    A SECOND IMPLEMENTATION LIVED HERE and a third in `run_analysis._site_jobs`, and both
    had the same defect independently: resolving which channel owns a half by MEMBERSHIP,
    which matches neither leaf on a campaign where both channels draw restock rules from one
    vocabulary, so every pair is skipped and the pass reports `0 coupled pair(s)` over a run
    that has many. That is the argument for the rule having one home.

    `[]` when nothing resolves, which the caller reports rather than guessing at: a pair
    whose halves cannot be found is a pair whose leaves were not both run.
    """
    halves = rt.arm_pair_halves(arm_pair, {ch: set(a) for ch, a in by_channel.items()})
    return [by_channel[ch][arm] for ch, arm in halves.items()]


def _coupled_pairs(rt) -> tuple:
    """`(pairs, dockless)` for a COUPLED run — `([], [])` on every uncoupled one.

    `pairs` is `[(label, leaf_dbs, site_db)]`, one entry per arm pair that HAS a site DB.
    `dockless` is `[(label, leaf_dbs)]` for everything that should have one and does not --
    a whole pair directory with no site DB at all, and (the hard case below) a single arm a
    leaf ran that no site-DB stem names.  ONE list, because both take the same rule: a
    defect when those leaves received, the ordinary inbound-off case when they did not.

    THE PAIRING COMES FROM THE SITE DB'S OWN NAME, which is the only declared record of it:
    `<store_arm>__<fulfillment_arm>` is the artifact's `{strategy}` capture (site-dock 24),
    inverted through `rt.arm_pair_of` and split by `rt.arm_pair_halves`.  There is
    deliberately no fallback that zips the two channels' arm lists -- site-dock 06's finding
    is that `sorted(set(arms))` destroys the rank the diagonal IS, so a pairing built that
    way is alphabetical, the right length, and wrong in a way nothing downstream detects.

    THE ARM PAIR THAT WROTE NO SITE DB IS THE HARD CASE, and reading the pairing off the
    filenames is what makes it hard: an arm pair whose `inbound_*.db` never reached disk is
    not ENUMERATED, so without the sweep below it would be neither FAIL nor MISSING but
    absent -- 33 of 34 pairs reported green while one lost its site scope, which is the
    defect site-dock 15 section 5 was written for, unreachable.  So every arm a leaf ran and
    no site-DB stem names is swept up here and handed on with `site_db=None`; the pairing
    for such an arm is unknown by construction, so the arm is reported under its own key and
    its leaves are the ones that ran it.

    A COUPLED INBOUND-OFF cell contributes no pairs at all, because it fields no dock and
    writes no site DB anywhere under the pair.  That is `dockless`, and the caller decides
    what it means: nothing, if its leaves never received; a lost site scope, if they did.
    """
    layout = rt.layout or {}
    if not layout.get('coupled'):
        return [], []
    by_pair: dict = {}
    for cell, cr, db in rt.sim_dbs():
        by_pair.setdefault((cell, cr.pair), {}).setdefault(
            cr.channel, {})[rt.strategy_of(db)] = db
    pairs, dockless = [], []
    for (cell, pair), by_channel in sorted(by_pair.items()):
        site_dbs = sorted(rt.site_inbound_dbs(cell, pair))
        if not site_dbs:
            dockless.append((f'{cell}/{pair}', sorted(
                d for arms in by_channel.values() for d in arms.values())))
            continue
        named: set = set()
        for sdb in site_dbs:
            arm_pair = rt.arm_pair_of(sdb)
            halves = rt.arm_pair_halves(
                arm_pair, {ch: set(a) for ch, a in by_channel.items()})
            named |= set(halves.values())
            pairs.append((f'{cell}/{pair}/{arm_pair}',
                          [by_channel[ch][arm] for ch, arm in halves.items()], sdb))
        # THE SWEEP.  Every arm a leaf RAN that no site-DB stem names, one entry per arm
        # rather than per pair -- which pair it belonged to is exactly what the missing file
        # would have said, so inventing one here would be the alphabetical-zip mistake in a
        # smaller disguise.  `reconcile_pair` decides whether it is a defect (that arm
        # received) or the ordinary case (it did not).
        for ch, arms in sorted(by_channel.items()):
            for arm in sorted(set(arms) - named):
                dockless.append((f'{cell}/{pair}/{ch}:{arm}', [arms[arm]]))
    return pairs, dockless


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('run', help='run root (a path, or a bare name under COMPARISON_OUTPUT_DIR)')
    p.add_argument('--verbose', '-v', action='store_true', help='per-arm numbers')
    a = p.parse_args(argv)

    try:
        base, rt, dbs = _sim_dbs(a.run)
    except Exception as exc:                                   # noqa: BLE001 - reported
        print(f'receiving_report: cannot resolve {a.run!r}: {exc}')
        return 2
    if not dbs:
        print(f'receiving_report: no sim DBs under {base}')
        return 2

    results = [reconcile(d) for d in dbs]
    #: db path -> its per-arm result, so the pair pass below can ask "did this leaf receive"
    #: without reconciling the file a SECOND time. On a 505,177-row arm check 6 alone
    #: re-prices half a million rows.
    by_db = dict(zip(dbs, results))
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

    # ── the PAIR pass ────────────────────────────────────────────────────────────
    # ZERO on every uncoupled run, and the line says `0 coupled pair(s)` rather than
    # nothing: an uncoupled run has no site, so a silent pass here would be the tool lying
    # by omission across the entire archive (site-dock 15 section 5). Every published run
    # is inbound-off and uncoupled, so this is the common case, not the corner.
    try:
        pairs, dockless = _coupled_pairs(rt)
    except Exception as exc:                                   # noqa: BLE001 - reported
        print(f'receiving_report: cannot group coupled pairs: {exc}')
        return 2
    p_failed, p_skipped = [], []
    for label, leaf_dbs, sdb in pairs:
        if len(leaf_dbs) < 2:
            print(f'  FAIL {label}: could not resolve both leaves of this arm pair from '
                  f'the arms on disk')
            p_failed.append(label)
            continue
        pr = reconcile_pair(leaf_dbs, sdb)
        if pr['verdict'] == 'FAIL':
            p_failed.append(label)
        elif pr['verdict'] in ('SKIP', 'MISSING'):
            # PRINTED, ALWAYS, and counted separately in the summary. A pair that was not
            # checked is neither a pass nor a failure, and the whole reason this pass exists
            # is that a tool which says nothing about a scope reads as a tool that checked
            # it. The verdict line below is skipped for these (they are never `active`), so
            # without this they would be invisible in both modes.
            p_skipped.append(label)
            print(f"  {pr['verdict']:4} {label}: {pr.get('note') or 'not checked'}")
        if pr['verdict'] == 'FAIL' or (a.verbose and pr.get('active')):
            print(f"  {pr['verdict']:4} {label}")
            if a.verbose:
                for ch, lf in sorted(pr['leaves'].items()):
                    # C and the run's own pick constant, side by side: C is
                    # `per_item + intercept` in seconds and pick_C is
                    # `pick_intercept + pick_per_item`, so a reader sees at a glance both
                    # that the two channels price differently and WHY they must.
                    c_s = _fmt_s(lf['unload_constant'])
                    k_s = _fmt_s(lf['pick_constant'])
                    # `pick_intercept` beside them, because the whole-leaf clause's strength
                    # depends on it: with the two intercepts EQUAL the implication
                    # "different pick constants => different unload prices" is exact, and
                    # with them unequal it rests on no cancellation occurring. The comment
                    # at that clause promises a reader can tell which branch they are in,
                    # and this line is the only place they could.
                    i_s = _fmt_s(lf['pick_intercept'])
                    print(f"        {ch:12} rows={lf['receive_rows']:,} "
                          f"secs={lf['seconds']:,.3f} C={c_s} pick_C={k_s} "
                          f"pick_intercept={i_s}")
                print(f"        site: {pr['site_batches']:,} batch row(s), "
                      f"leaf secs {pr['leaf_receive_seconds']:,.3f} vs site "
                      f"{pr['site_receive_seconds']:,.3f}"
                      + (f" - {pr['site_note']}" if pr.get('site_note') else '')
                      + (f" - {pr['site_price_note']}" if pr.get('site_price_note') else '')
                      + (f" - {pr['pick_price_note']}" if pr.get('pick_price_note') else ''))
            bad = [k for k, v in pr['checks'].items() if not v]
            if bad:
                print(f'        FAILED: {", ".join(bad)}')
                for key, rows in (('seconds', pr.get('site_seconds_mismatches')),
                                  ('counts', pr.get('site_count_mismatches'))):
                    for b, leaf, sit in (rows or [])[:5]:
                        print(f'          batch {b}: leaves {leaf} vs site {sit} ({key})')
    for label, leaf_dbs in dockless:
        # SHOULD HAVE A SITE DB AND HAS NONE -- a whole pair directory, or one arm no
        # site-DB stem names. Only a FAIL when those leaves actually RECEIVED: site-dock 06
        # couples the inbound-OFF pole too, which fields no dock at all.
        #
        # `active` and not `qty_events`: that key counts `work_events` rows ALONE, and the
        # completest form of the defect this rule exists for is a drain that reached NO
        # artifact -- no rows, no site DB, and `batch_stats.recv_unloaded` the only surface
        # that still remembers it happened. `reconcile` already folds all three into
        # `active`; a narrower witness here would read the total loss as the inbound-off pole.
        got = sum(1 for d in leaf_dbs if (by_db.get(d) or {}).get('active'))
        if got:
            print(f'  FAIL {label}: {got} leaf/leaves recorded receiving and NO site DB '
                  f'names them; the site-scoped rows were parked and never reached an '
                  f'artifact (ADR-0005)')
            p_failed.append(label)
    if pairs or dockless:
        print(f'receiving_report: {len(pairs)} coupled pair(s) with a site dock, '
              f'{len(dockless)} arm(s) or pair(s) without one, {len(p_failed)} FAILED'
              + (f', {len(p_skipped)} NOT CHECKED' if p_skipped else ''))
    else:
        print('receiving_report: 0 coupled pair(s) - this run has no site, so the '
              'cross-leaf clauses have no subject (they are not silently green)')
    return 1 if (failed or p_failed) else 0


if __name__ == '__main__':
    raise SystemExit(main())
