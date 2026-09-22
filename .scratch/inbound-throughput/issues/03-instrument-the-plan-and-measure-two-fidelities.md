# 03 - one instrumented probe, two fidelities measured offline

Type: research
Status: open

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
