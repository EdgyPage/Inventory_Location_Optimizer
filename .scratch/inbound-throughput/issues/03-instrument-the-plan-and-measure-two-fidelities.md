# 03 - one instrumented probe, two fidelities measured offline

Type: research
Status: resolved

The exact `plan_order` costs T(T+1) `place_load` calls per drain and exact memoisation is
refuted (`the-gain-sweep-cannot-be-made-incremental`). Two cheaper plans are on the table and
neither has a number for the campaign's families (Q10, decision: measure before building):

- **(i) a pool-free adapter for the selector families** -- the `_place_merge` rung, which the
  fidelity ladder proved order-equal to the full pool (28 of 29) for tmin/tmax only. The map's
  fog: `rank_cartlabor` balances aisles and `rank_minlabor` chooses by a heap, so they choose
  different bin SETS than an extremal-D slice; the proof does not carry over.
- **(ii) a top-m plan** -- keep the pool, but each round re-evaluate only the m best candidates
  on their stale gains: T + (T-1)*m placements instead of T(T+1). Fidelity unknown; the
  refutation counted shared BINS (an upper bound on exact reuse), not order changes.

One trace answers both, and any variant of (ii) for any m, offline.

## What to build

- A per-round trace from `plan_order`: per drain, per round, every remaining candidate's
  `(trailer seq, now cost, defer cost, gain)` and the winner -- and for (i), the now-side
  placement's `(aisle, x_phys, score)` takes per candidate, so a pool-free re-placement can be
  scored against them bin by bin. Written as a DECLARED run-tree artifact beside the site DB
  (assumption accepted round 5): a new leaf in `Optimization/runschema/schema.py`, `--sync`
  before, `--accept` after, resolved through `runschema.resolver_for`, never a joined path.
  Off unless the probe cell turns it on: the trace at depth 17 is 306 rows per drain and
  must cost the campaign nothing when off (byte-identical by the toy digest with it off).
- The probe cell: `_probe_plan_trace` in `whatif_config`, the pattern of `_probe_unload_ref`
  and `_probe_trailer_bound` -- `k1_off_gmyopic` with the trace on, the winner pair, both
  stock modes, from a `git archive` snapshot per `detached-runs-import-the-working-tree`.
  ~3 h at campaign scale on the reference catalogue (`phase2-binds-the-reference-catalogue-
  not-the-default`: pass `--profiles-dir`).
- The offline scorer, kept as this ticket's asset under `.scratch/inbound-throughput/assets/`:
  for (ii), replay the greedy from the trace using stale gains and re-evaluating only the top
  m, for m in {1, 2, 4, 8}, and report Kendall tau and top-1 agreement per drain against the
  exact order; for (i), re-place each candidate pool-free over the same frozen state and
  report bin-set agreement and the resulting order's tau.

## Bar (from Q9)

A table per family and per variant: tau median, top-1 rate, and the placement count per
plan. The build ticket (04) takes whatever clears tau >= 0.9 median and top-1 >= 0.8 at the
lowest cost; if both clear, both, in the order of cost. If neither clears, that is the
answer and ticket 04 is re-scoped here before anything is built.

## Answer (2026-09-23): neither reduction clears the declared gate

Probe `comparison_whatif_20260922_191406` (`_probe_plan_trace`, reference catalogue, fifo +
gmyopic + gforecast, winner pair + rider, both stock modes, every 4th batch traced), finished
2026-09-23 02:29.  Scored by `.scratch/inbound-throughput/assets/score_plan_trace.py`: 144
plan records, 36 winner-pair (pool-family) plans per gain policy, yard depths 14-25 for the
yard plan and 4 for the dock plan.  The rider pair's plans are fifo-restock plans and score
trivially; the gate reads the pool family.

| policy | reduction | tau med | top-1 | wall vs exact | first wrong pick loses (median, of the round's gain spread) | p90 regret, of the winner's gain |
|---|---|---|---|---|---|---|
| gmyopic | merge | 0.585 | 75% | 0.04x | 1.4% | 18.7% |
| gmyopic | top-1 stale | 0.640 | 100% | 0.31x | 0.9% | 21.5% |
| gmyopic | top-2 stale | 0.558 | 100% | 0.43x | 1.9% | 9.9% |
| gmyopic | top-4 stale | 0.674 | 100% | 0.50x | 1.1% | 38.7% |
| gforecast | merge | 0.671 | 39% | 0.05x | 15.6% | 219% |
| gforecast | top-1 stale | 0.477 | 100% | 0.14x | 34.6% | 2463% |
| gforecast | top-2 stale | 0.739 | 100% | 0.22x | 5.5% | 1198% |
| gforecast | top-4 stale | 0.835 | 100% | 0.34x | 44.3% | 1317% |

The exact plan cost 5,308 s (gmyopic) and 9,617 s (gforecast) over its 36 traced plans --
146-267 s a plan.  Gains rose between rounds on 10.2% (gmyopic) and 1.8% (gforecast) of
candidate pairs; the median winner margin is 2.5% and 4.7%.  81% of gforecast's candidate
gains are below zero, which is why its regret is read against the round's gain spread.

**Reading.**
1. **The Q9 gate (tau >= 0.9 median, top-1 >= 0.8) is met by nothing**, and the fog is answered:
   the pool-free merge rung is NOT order-equivalent for the selector families -- its first pick
   matches the exact plan on 75% (gmyopic) and 39% (gforecast) of plans, against 28 of 29 on the
   extremal-D ladder.
2. **For gmyopic the disagreement is mostly cheap**: every reduction's first wrong pick costs a
   median ~1-2% of the round's spread -- near-ties reordered -- but the p90 is 10-39% of the
   winner's gain, so some drains do take a materially worse trailer.
3. **For gforecast the reductions are wrong in substance**: committing the first trailer moves
   every other trailer's forecast gain, and stale or merged gains pick badly.
4. Tau over the whole order penalises positions a drain never stages (it stages up to its free
   doors and replans the next day); set@4 is 68-86% across the board.

**Ticket 04 is therefore not buildable as specified**: the reduction it builds is "the one the
probe picks", and the probe picks none under the declared gate.  The user is researching a
reframing of the problem statement (2026-09-22); 04 waits on it.  Nothing here was re-gated.

**Inertness** (the trace changes no simulation): see the digest line below.
- **Inertness: IDENTICAL.** `run_digest.py --cell` of `k1_off_fifo`, `k1_off_gmyopic` and
  `k1_off_gforecast` against the finished campaign root `comparison_whatif_20260920_150203`:
  all three cells IDENTICAL on 8 arms each.  Tracing a plan changes no plan.
