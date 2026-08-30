# Prototype the load-score evaluator

Type: prototype
Status: resolved
Blocked by: 01, 03, 10

## Question

Prototype the GAIN evaluator the unload plan orders by (the objective resolution, 10):

    gain(t) = E[put + pick work if t's load places from the pool NOW]
            − E[same if deferred to the NEXT drain's pool (leftovers + predicted clears)]

FAITHFUL-TO-ARM: both expectations estimate the bins THIS arm's pool would grant (a virtual
copy of the arm's own placement machinery over the SpaceView's two tiers), never an
idealized best-bin cost — pools disagree with the cost model (the travel-D pools ignore the
height term entirely), and a plan optimized against a rule the arm doesn't use optimizes a
fiction. The pick side carries E[visits] ≈ quantity / per-visit draw; the put side is
travel-dominated and paid once; cost-model weights, no new knobs.

Prototype the fidelity ladder: full greedy-sequential virtual pool (contention-aware,
consumes bins as the load places — the plan's virtual-consumption step needs this shape)
versus independent per-item best-bin scoring (cheap, ignores contention) — and find where
between them the resulting ORDERING stops changing, which is the only fidelity that
matters. Measure computational cost at realistic scale (standing trailers × items per load
× candidate bins) — this number is the caching stakes and feeds the cache-boundary ticket
(06). Recommend the evaluator contract: inputs (frozen view, load, the arm's placement),
output (gain — one comparable number per candidate), and cost.

Link the prototype under `assets/`. Consult `codebase-design` and the reuse list (CLAUDE.md
§2: `regime_of`, `BinKey`, the cost model) before inventing any scorer.

## Comments

2026-08-29, from resolving "Design the space timeline" (03): predictions are UNTIMED — the
unload-window horizon is gone (question edited above). And the objective the score serves is
reopened in "Define the inbound objective" (10, now blocking): placement quality may be
vacuous for trailer ordering, so the evaluator contract must be argued against whatever 10
adopts (on-shelf availability / unload-plan candidates).

2026-08-29, from resolving "Define the inbound objective" (10): question rewritten above —
the objective is expected future work (put + pick), the output an unload plan with
set-composition semantics, the evaluator faithful-to-arm; the original "two candidate
shapes" survive as the fidelity ladder. Load-bearing facts for the prototype, verified
during 10: no bin is chosen during the receive drain (units enqueue in canonical merged
order, bins are allocated in one `_drain_putaway → _stock` pass, ranked waves served by the
pool's own `sort_key` precedence), so gain is about pool MEMBERSHIP, not seats; reclaim
runs at drain step 0, so `predicted` is exactly the next drain's pool gain. All three
blockers are now resolved — this ticket is on the frontier.

## Answer

Resolved 2026-08-29. The prototype is
[`assets/prototype_gain_evaluator.py`](../assets/prototype_gain_evaluator.py) — runnable
from the repo root with `python .scratch/inbound-optimization/assets/prototype_gain_evaluator.py`,
seeded, throwaway, kept as this ticket's asset (the inbound-groundwork precedent, not a
branch). Faithful-to-arm is taken literally at the top rung: it drives the repo's actual
`_RankedAssignPool` (tmin) over stub shapes copied from
`Tests/unit/test_ranked_assign_pool_equivalence.py`, and prices every (unit, bin) pair with
the real cost model (`per_pick`, `handle_var`, `height_multiplier`, `SpeedProfile`,
repo-default coefficients). The plan loop is 10's greedy verbatim, four fidelity rungs:

- **L3 full-pool** — the arm's own pool per BinKey class per evaluation, served in
  `sort_key` order (co-occurrence live), virtual consumption of the chosen load's takes.
- **L2 merge** — contention-aware, pool-free: the load's units take the class's k
  cheapest current bins by the arm's own D, paired in freq × labor_cost priority order;
  same consumption.
- **L1 indep+consume** — every unit of a class priced at the same head bin (no
  within-load contention), consumption between plan steps only.
- **L0 static** — independent best-bin, no consumption; the plan is one sort.

**Finding 1 — the ordering stops changing at L2.** Across the scale grid (6×30×120,
12×100×300, 24×300×600 = trailers × units/load × bins/class), 10 extra seeds at mid scale,
a scarcity stress (60 bins/class, load volume ≫ supply), and an unbiased-predicted-tier
robustness pass — 29 unique (scale × seed) comparisons — L2 reproduced L3's plan
**exactly in 28 of 29** (Kendall tau 1.000); the single divergence was a head near-tie
swap (top-half set agreement still 1.00). L1 flips the step-0 argmax routinely
(tau 0.58–0.94) and L0 is far off (tau 0.27–0.73, first divergence at position 0–1):
**within-load contention + cross-step set consumption is the fidelity that matters; the
pool machinery itself is not.** This is 10's "membership, not seats" made quantitative.

**Finding 2 — the residue is the co-occurrence term, and only near-ties move.** Killing
β·Δlift in L3 makes L2 ≡ L3 exactly on the divergent seed. Structural reason: for the
extremal-D pools, repeatedly taking the min over per-aisle sorted heads IS the k-way merge
— the taken SET is the k cheapest bins regardless of unit order — so priority pairing and
aisle bookkeeping redistribute visits over the same bins and cancel in the gain
difference; only the affinity term perturbs near-tied gains.

**Scope of the equivalence, stated honestly:** proven for the extremal-D family
(tmin/tmax: no selector, default order key) only. Selector arms choose different SETS —
rank_popularity's aisle selector and rank_random's RNG draw — so there the pool-backed
rung is the reference; and since an ordering entry may consume no RNG (the 12 purity
contract), rank_random's virtual pool must price by EXPECTATION over aisle heads, a
decided deviation to record at build time. Not modeled: zoning sub-groups, tier spill;
exhaustion used one shared fallback across rungs (worst-bin × 1.5), so the ladder says
nothing about spill pricing — the build should resolve tiers over `SpaceView.empties`
keys the way `_candidates` does.

**Finding 3 — cost at realistic scale (measured, one plan = one drain).** The greedy is
O(T²) gain evaluations, each O(U log B + A): naive L3 (pool rebuilt per candidate × step)
= 9 / 93 / 650 ms per plan across the grid; L2 = 1.6 / 19 / 156 ms; L0 = 0.4 / 3 / 22 ms.
Per arm-run (~100 drains at N_BATCHES=100): L2 ≈ 2–16 s, naive L3 ≈ 9–65 s — both small
against an arm's sim wall-clock. **The caching stakes are MODEST** (posted on ticket 06):
the 4–6× naive-L3-vs-L2 gap is exactly what sort-once-per-class-then-slice recovers, an
evaluator-internal structure, not a cross-drain cache.

**The recommended evaluator contract** (what the build ticket implements):

- **Seam**: one `@ordering` registry entry per gain family — myopic = gain against
  `empties` only, forecasting = deferral pool includes `predicted` — implementing 10's
  greedy; returns the permutation; `bounded_order` composes bound-first (12, unchanged);
  called once per drain.
- **Inputs**: `ctx.space` (the frozen two-tier SpaceView, BinKey-keyed); the candidates'
  load compositions (derivable at plans-at-arrival — the pack plan is fixed per trailer);
  the ARM'S PLACEMENT bundle **injected by the driver** (`open_pool` + wp + freq/qty
  tables + frozen copies of `aisle_sku_sets`/`aisle_idx_sets`/`aisle_demand_sum`) — the
  `drain_sku` injection precedent, since `Inbound -> Warehouse.placement` is forbidden;
  cost-model weights (put SpeedProfile, PickConfig coefficients, height brackets) — no
  new knobs.
- **Output**: gain(t), one comparable float per candidate per greedy step. Kind SCORE,
  policy-relative; never persisted as a metric column.
- **Pricing**: put = travel at the put crew's SpeedProfile, paid once; pick =
  E[visits] × (travel at pick speeds + `per_pick(height_mult, intercept, handle_var,
  draw)`), E[visits] = qty / per-visit draw.
- **Fidelity, per arm family, behind one internal seam** ("virtually place a load
  against a pool state"): extremal-D arms use the k-cheapest merge structure (sorted-D
  slice — provably order-equal up to co-occurrence near-ties, 5× cheaper, naturally
  incremental under consumption); selector arms use the arm's own pool over copies. The
  plan loop cannot tell which adapter it got.
- **Purity**: mutates no manager state, consumes no RNG, reads only the frozen ctx.
