# Round 3 closeout

Label: wayfinder:map

The ordered work lives in [PLAN.md](PLAN.md) beside this file; this map holds the
destination, the decisions, and the fog.

## Destination

Nothing this round produced is left unverified, unrecorded, or lying around -- and the two
questions it opened but could not answer are either answered or handed over with the measurement
that would settle them named.

Reached when:

1. The full routine suite has run WITH the conftest fixture, and every remaining failure is
   attributed to a cause, not a guess.
2. The derived layer is in sync and the results drive carries no litter from this session.
3. The durable facts are in memory, so the next session does not rediscover them.
4. The `R x A` attribution is settled by a COUNT on the deep tier, or closed with the reason.
5. `delta_lift_idxs` -- convicted in every ladder and never touched -- is refactored or closed.
   DONE 2026-09-16: closed with a measured reason, and the instrument that mis-ranked it fixed.
6. What is NOT ours (the pre-existing architecture drift) is written down where its owner will
   find it, rather than mentioned once in a conversation.

## Notes

- **Phase A is not optional and goes first.** Leaving my own verification and litter outstanding
  is worse than any new finding. The conftest fixture in particular is UNVERIFIED at suite scale
  and its whole risk is a test that silently depended on a predecessor's mutation.
- **Order within A is chosen for the host**: resync the derived layer (else the suite reports
  drift that is already known), launch the suite, and do disk + memory work alongside -- the run
  trees live on the results drive, the suite reads the repo drive, so they do not contend.
- **Byte-identity discipline holds** for every refactor in phase C, with the named-break escape
  from this round's standing decision.

## Decisions so far

- **`delta_lift_idxs` is CLOSED, not refactored** (2026-09-16, destination 5). Its k = 1.59 is a
  bounded ratio caught mid-saturation, not a complexity class: one parent (`_reclaim_empty_bins`,
  agreed by all six captures, and NOT the placement path the plan named), per-call cost capped by
  `top_k` + `cluster_size` and measured flat at 11.9 -> 10.0 elements across a 16x catalogue, and a
  call count bounded by reclaimed bins whose ratio runs 0.169 -> 0.871 toward a guaranteed ceiling
  of 1.0 with local exponents falling 1.92 -> 1.30. The planned fix was a NAMED COMPARABILITY
  BREAK; it would have converted linear into linear. Full argument in
  `docs/design/COMPLEXITY_ROUND_FINDINGS.md` section 3. Commit `d7f3979a`.
- **The trend is now part of the instrument** (same commit). A fitted exponent cannot distinguish a
  complexity class from a ratio approaching a ceiling, so `_trend()` reports the direction of the
  local exponents the ladder already computed, and a converging series sorts last within its cost
  class. It is still listed -- the tool reports, the reader judges.
- **The offender table has exactly ONE live candidate.** Re-triaging all seven flagged offenders by
  the same test: five are converging (two of those also too small to matter at any ladder scale),
  `per_pick` is closed on price, and only `_TravelBalancedPool._aisle_best` has a RISING local
  exponent (1.62 -> 1.77). That is the site this round already half-fixed, and destination 4 is
  precisely its open question -- so A and C converge on the same next measurement.

- **The remaining Phase C candidates are not small, they are UNTRACED** (2026-09-16). The meso
  ladder runs one arm, so `_CoDemandPool` and `_ClusterMapPool` appear in no HEAD artifact at all.
  The deep ladder's slowest arm at every rung is in that one family, and its `total_s` diverges
  (local k 0.98 -> 1.61) while the 136-arm sum stays linear at k=1.05. Stated as a LEAD: a max over
  a migrating argmax is not any arm's curve. Commit `93e14c35` fixes both halves -- the ladder now
  keeps `per_arm_total_s` and fits every arm, and `--config cluster_map` / `--config cmin` make the
  family reachable from the meso tier. Commit `7187e6f2` records it.
- **Inbound's two quadratics are closed** (2026-09-16, commit `7187e6f2`). `bounded_order`'s
  quadratic branch is dead in every shipped configuration (`INBOUND_TRAILER_BOUND = None`, and the
  per-pallet call sites pass `bound=None`, which is the `sorted` path). `plan_order` IS cubic, and
  the plan's three mechanical fixes do not remove it -- the `taken | others` union is the floor, so
  the real fix is an API change to `place_load`. Both scale with rho, not with the catalogue, so
  the SKU ladder cannot see either however far it is extended.

## Fog

- Does the conftest fixture break a test that silently depended on a predecessor's mutation?
  Unknown until the suite runs. Such a failure is a FINDING, not a regression, but it still has
  to be triaged one by one.
- ANSWERED: neither. The call count grows, the per-call cost does not, and the count is a ratio
  saturating against a ceiling the code guarantees. See Decisions.
- Does `_aisle_best` keep accelerating past 8,000 SKUs, or saturate like the rest? Its local
  exponent rises 1.62 -> 1.77 on the meso ladder, but the deep tier carries NO function counts, so
  the only instrument that reaches deep scale cannot see it. Destination 4's `_FLOW_COUNTS` entry
  is the measurement that answers both questions at once.
- A meso rung above ~13,000 SKUs should read k -> 1.0 for `delta_lift_idxs`. That is confirmation,
  not evidence -- the ceiling argument is a proof from the code and does not depend on it.
- Does the cluster family actually diverge, or were four arms taking turns being unlucky? The two
  new meso cells answer it in minutes each, and they are the NEXT measurement once the host is
  quiet. If they do diverge, the per-take scans the plan named on static grounds finally have a
  measured denominator -- and `reord_s`'s unattributed deep bend has a candidate.
