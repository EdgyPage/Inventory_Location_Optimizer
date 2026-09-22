# 04 - build the evaluator reduction the probe picks, and re-key the bins on the way

Type: task
Status: open
Blocked by: 02, 03

Ticket 03 says which of the two reductions (pool-free adapter for the selector families;
top-m plan on stale gains) clears the order-agreement gate, and at what cost. This builds it.
The user's decisions: REPLACE the exact evaluator under the existing policy names, a new era
(Q9); the exact plan survives behind a fidelity knob on the policy, set by a probe cell the
way `k1_off_gmyopic_k8` sets `INBOUND_TRAILER_BOUND` (`_policy('gain_myopic', ...)`).

## What to build

- The reduction(s) ticket 03 chose, behind `_Evaluator`'s one internal seam ("virtually
  place a load against a pool state"), so `plan_order` cannot tell which adapter it got --
  the contract `inbound-optimization` 04 wrote.
- **Re-key bin identity.** The evaluator addresses bins by `id(bin)` at 28 sites across
  `Inbound/gain.py`, `Warehouse/placement/Assignment_Functions.py` and `frozen_tier.py`
  (`ev.taken` is `set(map(id, takes))`; the leave-one-out exclusion is set algebra over ids).
  A stable key (the frozen tier's own index is the natural one) is what lets a helper
  process (ticket 07) hold a replica at all, and the adapter touches every one of those
  sites anyway; doing it here means doing it once. Byte-identical by the toy digest: a key is
  not arithmetic.
- The fidelity knob: `inbound_gain_fidelity` on the inbound spec, `exact` | the new rung, the
  five seams of a config knob (`config-knob-has-five-seams`: `settings.py`'s four plus
  `workunits._shared`). The probe cell that sets it to `exact` joins `whatif_config` beside
  the trailer-bound cell so the two evaluators can be run side by side on demand; the
  campaign matrix never carries both (Q9: "two evaluators in the matrix double the cells for
  a policy question that has only one answer").
- The ADR (Q28), titled on the decision: "unload policies are planned by an approximate
  evaluator gated on order agreement". Hard to reverse once results publish under it,
  surprising without ticket 03's table, a real trade-off; `docs/adr/0007-...`.

## Bar

- Ticket 03's scorer re-run on a trace from the new evaluator reads tau >= 0.9 median, top-1
  >= 0.8 against the exact plan (gate).
- `_probe_unload_ref` (fifo + gmyopic, campaign scale) under the new evaluator: overage and
  pick-owed within ticket 01's measured floor of the exact evaluator's on the same cells
  (acceptance); the priced unit's wall against the 7,803 s / 10,449 s of record.
- The toy `_toy_priced` digest IDENTICAL with the knob at `exact`; the three pool families'
  slice oracles green; `test_placement_selection_is_not_a_scan.py` either still pins the
  count or is re-pinned WITH the reason, never deleted.
- Memories `aisle-best-is-what-a-pool-open-now-costs` and `the-gain-sweep-cannot-be-made-
  incremental` updated: the first names the next target, the second's "exactly one lever" is
  no longer true.
