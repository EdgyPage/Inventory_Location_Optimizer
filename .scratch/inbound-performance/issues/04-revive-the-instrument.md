# Revive the instrument: the oracle, the clock, the tier, and a yard that stands

Type: task
Status: resolved

Four edits, all in `Tests/`, no production code touched. Together they take the calltree framework
from "cannot see `Inbound/` at all" to "runs the standing yard and counts its flows".

## 1. The stale frozen oracle — FIXED

`test_rank_cache_equivalence.py::test_minlabor_cache_matches_frozen_oracle` failed on clean
`develop`. **The oracle was stale; production was correct.**

`fc7a46a5` (ADR-0001, the per-item charge) re-priced all four production minlabor sites to
`per_pick(m, intercept, var, 1, per_item)`. It updated the SIBLING oracle in
`Tests/unit/test_travel_balanced_equivalence.py` in the same commit — with the exact line
`per_item = wp.pick_per_item  # ADR-0001: the oracle prices what the pool prices` — and missed the
two frozen functions in `Tests/calltree/`, because that file is hand-run and in none of the nine
gates. The two sides of the comparison were literally running different cost formulas, so the test
reported the CACHE as diverged when the cache was fine.

**Proof it is the right fix and not a green-making one:** re-pricing only those calls makes the
frozen oracle reproduce production's end state byte for byte — all four tuple elements, digest
`0d04c80f5298b084890ec1565cc2bd7806450a7c071ab8fe777e3052f33f58e0`. The charge is not a constant
offset argmin would ignore: `per_pick` returns `mult*(intercept + qty*per_item + qty*var)`, so the
term is scaled by the height bracket, and this scenario spans multipliers {1.0, 1.2, 1.4} at
`per_item` 0.5 — enough to flip which bracket end wins inside an aisle.

Both frozen functions were re-priced. `_unrefreshed_ranked_minlabor_impl` is an oracle too, and the
mutation canary passes **vacuously** if only one moves; measured, the canary still fires after both
were corrected.

## 2. `run_meso` had no clock — FIXED

`check_reorders` assigns `self._now_s = now_s` unconditionally and `now_s` defaults to None.
`run_meso` never passed it, so `mgr._now_s` was None on every drain. Harmless under a `BatchTransit`;
**silently fatal** the moment a trailer pipeline is bound:

* `Trailer.arrived` returns False whenever `now_s is None` (trailer.py:193), so a trailer with ANY
  positive lead **never arrives** — measured before the fix: 73 trailers stuck in `_in_transit` over
  10 batches, zero unloads.
* A lead-0 trailer does arrive but stamps `arrived_s = None`, and every standing priority key reads
  that stamp. `_fifo_standing` / `_lifo_standing` both degenerate to a constant — making fifo and
  lifo a **byte-identical** unload stream — and `gain_gated`'s urgency filter
  `t.arrived_s is not None` admits nobody at any threshold.

Either way the fixture looks healthy and measures nothing.

The loop now carries `arm_clock`, advanced by each batch's makespan (`BatchStats.duration`).
**Byte-identical on the flag-off path by construction, not merely by measurement:**
`BatchTransit.dispatch`'s own docstring says "the batch countdown ignores both", and neither
`dispatch` nor `release` reads `now_s` — the parameter exists only so the two transits are
indistinguishable to the phase bodies.

## 3. `run_fullfid` was dead — FIXED

Removed `max_bins=20000, min_bins=5000`. A cap that binds below the declared levels refuses the plan
rather than fielding less, and the tier had been dead since levels became a declaration.

**No cap value would have saved it**, which is the non-obvious part: `era_coverage.fixed_point`
declares before it converges, and its SEED round sizes ~11x the plan it settles on — 898,700 bins
before settling at 77,500 at `max_skus=300` — so any cap under ~900k refuses on round 1 whatever the
final warehouse costs. `coverage_days` and `max_skus` are the size knobs; a bin cap is not one.

Verified: the tier now completes on `perf_mixed_40k` — 300 SKUs, 4 batches, real
`_run_strategy_worker`, in 27 s.

## 4. `build_assets` can now build a standing yard — NEW

Fourteen keyword-only parameters, every one defaulting to the off state, mirroring the driver's own
single-leaf construction site rather than paraphrasing it: `YardTransit`, `SpaceTimeline.attach`,
the packer, `SiteReceiving`, and the gain bundle only when a gain policy is named — built through
the driver's own `_gain_bundle_for` rather than a second implementation.

Two loud refusals, both copied from `inbound_spec`'s: a yard with no receiving crew, and a
`lead_spread` with a zero median (silently inert, since the draw is `median * exp(sigma*Z)`).

This reaches the standing drain because `_receive` reroutes to the coordinator whenever the bound
transit carries `STANDING` (inventory_reorder.py:675), and that branch takes ONE leaf. No coupling,
no `bind`, so `site_scoped` stays False and the meso loop's per-batch accessors keep working.

## Answer: the yard stands, and the quadratic is measurable

`build_assets(..., inbound=True, recv_crew=2, dock_doors=4, yard_policy='gain_forecast',
dock_policy='gain_forecast')`, 600 SKUs, 10 batches, seed 7:

| whistle (`recv_deadline`) | final yard depth | entry calls | `place_load` per entry | pools per `place_load` | `_make_pool` total | placements |
|---|---|---|---|---|---|---|
| none | 0 | 18 | 88.3 | 5.34 | 8,496 | 8,519 |
| 80 s | 38 | 18 | 327.9 | 4.46 | 26,306 | 6,523 |
| 30 s | 59 | 18 | **733.4** | 4.96 | **65,496** | 5,230 |

Three things this establishes:

1. **`plan_order` costs T(T+1) `place_load` calls.** Inverting the measured ratio gives implied T of
   8.9 / 17.6 / 26.6 against measured mean candidate counts of 7.4 / 12.6 / 18.3 — the implied value
   runs above the mean because the quadratic is dominated by the large entries, which is exactly
   what a T^2 law predicts.
2. **Each `place_load` opens ~4.5-5.3 pools**, so pool opens run at roughly **5·T^2 per entry call**.
   At the tightest whistle a single entry call faced 62 candidates: 3,906 `place_load`s and ~19,500
   aisle-dict copies, in one drain.
3. **The work is anti-correlated with useful output.** Pool opens rose 7.7x while placements FELL
   8,519 -> 5,230. The evaluator gets more expensive exactly as the warehouse gets less done.

**The whistle is the only lever that stands a yard.** `_unload_split`'s loop has exactly one exit
that is not "nothing workable anywhere" (`receiving.py:1078`), so with `recv_deadline=None` every
freed door immediately pulls the next yard trailer and the yard empties inside every drain —
regardless of doors, crew size or trailer type. That refutes the growth knobs this effort's plan
originally proposed.

## Comments

Counting correction, recorded because it nearly shipped: the first probe wrapped BOTH
`gain.plan_order` and the four registry entries, and the entries call the module global — so every
entry call was counted twice and the first reported fan-out (44 / 164 / 367) was exactly half the
truth. One counter per symbol. The shape of the finding was unaffected; the numbers were not.

## The flag-off proof

CLAUDE.md §2 requires a new feature to be a strict no-op when its flag is off. Proven, not assumed:
`build_assets(n_skus=400, bins_per_aisle=40, coverage=2.0, safety=0.4, seed=7)` + `run_meso(6)` run
in two fresh processes, one against the current module and one against `git show HEAD:` of the same
file, digesting the full end state — every occupied bin's (aisle, x, y, sku, qty) in geometric
order, plus the four loop counters:

```
CURRENT   picks=2569 placements=3903 reorders=157 skipped=0
          3ff726d4f7d536e14cc300272b6a8a1f14b755e2e9039559d41085bf0ba05bca
HEAD      picks=2569 placements=3903 reorders=157 skipped=0
          3ff726d4f7d536e14cc300272b6a8a1f14b755e2e9039559d41085bf0ba05bca
```

Identical. Separate processes on purpose: `build_assets` reseeds but also mutates class-level
counters (`Order.next_sku`, `Aisle.next_aisle_id`), so two variants in one process would not be an
honest comparison.

The tracer carve carries its own version of the same proof, as a test rather than a one-off:
`test_the_carve_is_a_partition_and_is_inert_when_empty` asserts both that the partition holds and
that an empty `CARVE_MAP` reproduces the pre-carve attribution exactly — so no archived capture
silently re-attributes.

And the carve got the anchor gate it would otherwise have lacked
(`test_carve_map_symbols_resolve`, `test_a_carve_section_is_not_in_the_runner_vocabulary`). Without
it a rename would not raise; it would make `t_inbound` read 0.0, which reads as "the inbound drain
never ran" — the same silent-rot shape that let the stale oracle stand.

## Verification

* `python -m pytest Tests/calltree -q` -> **22 passed** (was 1 failed / 21 passed at Stage 0),
  isolating the oracle fix against the pre-edit scenarios.
* `python -m pytest Tests/calltree/test_calltree_anchors.py -q` -> **10 passed**, including the
  three new carve gates.
* `run_fullfid` completes on `perf_mixed_40k` (300 SKUs, 4 batches, real `_run_strategy_worker`).
* The inbound path builds and drains: `YardTransit` STANDING, `SiteReceiving`, `SpaceTimeline`,
  `OneOwnerBundle` all bound, and the flows are non-zero.
