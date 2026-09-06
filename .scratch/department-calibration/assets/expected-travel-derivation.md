# The expected-travel closed form — derivation for review

Ticket: [Derive the expected-travel closed form](../issues/13-derive-the-expected-travel-closed-form.md).
Status: **derivation + development-time check done; §7 records what has been built since the four decisions.**
The check script is [validate_closed_form.py](validate_closed_form.py) (takes a pair dir, a leaf
DB, a channel and a day window; env `CV`, `ONEWAY`, `SMEAR`, `EMP`, `PUT` select the variants
below). Every number here was produced by it against the passes ticket 09 left on disk.

## 0. What the simulator charges (read from the code, not assumed)

One **task** is one aisle visit (`Task.from_batch`, one per aisle a batch touches). The
picker starts each task at the aisle mouth `(0, 0)`, walks the bins in `_plan_aisle_path`
order (columns ascending in x; inside a column ascending or descending in y, whichever end
is nearer the current y), pays `|Δx|·x_pace + |Δy|·y_pace` for every segment — **including
segments to bins that turn out empty** (the `snap_qty == 0` `continue` sits *after* the
travel) — a `cart_swap_coef` for every next-fit swap (`cart_step`, the cart carries across
tasks and batches), `M(y)·(intercept + q·per_item + q·var)` per non-empty bin, and on a
**one-way lane** an exit of `(L − x_last)·x_pace + y_last·y_pace`. There is **no travel
between tasks**: every task resets to the mouth. `s_pick` is Σ task seconds ÷ Σ units.

Consequences the derivation leans on:

- On a two-way lane the path is monotone in x from the mouth, so x travel per task is
  exactly `x_max` of the visited bins. On a one-way lane it is exactly `L`.
- Handling and swaps are already closed-form in the catalogue; the only new work is the
  per-task `x_max`, the y walk, the exit, and the count of tasks and bin visits a day of
  lines induces.
- The per-line quantity the sampler draws is `max(1, Poisson(λ))`, whose mean is
  `λ + e^{−λ}` — not `max(1, λ)`, which `analytic_pick` uses today (a 0–2% bias at small λ).

## 1. Inputs, all known at setup

| symbol | meaning | source |
|---|---|---|
| `π_s` | line share of SKU s in its channel section: `w_s / Σ w` | catalogue `relative_frequency` (affinity lift ignored — it redistributes lines, it does not add them) |
| `λ_s` | Poisson rate of units per line | `quantity_rate` |
| `var_s`, `vol_s` | handling term and unit volume | catalogue + the channel's pick config |
| `B_s = [(b_1, c_1), (b_2, c_2), …]` | the SKU's bins in **drain order** (forward-pick first, then location) with their quantities | the placement (see §4 for which) |
| `(x_b, y_b)`, class, `C`, `R`, `L` | bin position, BinKey, columns, rows, aisle length | the BUILT warehouse (`aisle_layout` + `Aisle.Bin.x_phys/y_phys`) |
| `n`, `cv` | lines per day and its day-to-day coefficient of variation | the era: `n` is the unknown of the fixed point; `cv` is the channel's declared `std/mean` (1/3 store, 1/4 fulfillment) |
| paces, `one_way`, cart capacity, `cart_swap_coef`, brackets | | the channel's pick config |

## 2. The expectation

**Lines → bin visits.** A line for s drains its bins in order; bin k is reached iff the
line's quantity exceeds the stock before it: `reach_k = P(q > c_1 + … + c_{k−1})`, a Poisson
tail (`reach_1 = 1`). Expected units drawn from bin k are
`Σ_{j=C_{k−1}}^{C_k − 1} P(q > j)` (with `P(q > 0) = 1`), and the second moment
`Σ (2(j − C_{k−1}) + 1)·P(q > j)` over the same range. Both are `gammainc` sums.

**Poissonisation.** With `n` i.i.d. lines a day, bin b of SKU s is visited with rate
`r_b = n·π_s·reach_b` and the bins are independent Poisson streams; the visit probability is
`v_b = 1 − e^{−r_b}`. (Exact for i.i.d. draws; the sampler draws distinct SKUs without
replacement, which lowers `v` by O(π_s²) — negligible at 10⁵ SKUs.) A day's line count is
itself random, `N ~ Normal(n, cv·n)` clamped to ≥ 1; every aisle expectation below is
averaged over N by 7-point Gauss–Hermite quadrature (rates scale linearly in N, so this is
one rate matrix scaled seven times). Skipping this over-predicts distinct aisles by ~3 %
(Jensen: the visit count is concave in N).

**Per aisle** (columns `c = 1..C` at `x_c`, rows `r = 1..R` at `y_r`, ground `y_0 = 0`):

- column visited: `u_c = 1 − Π_r (1 − v_{c,r})`; aisle visited: `P_a = 1 − Π_c (1 − u_c)`;
  **expected tasks per day** `= Σ_a P_a`.
- **x travel**, two-way: `E[x_max] = Σ_c x_c · u_c · Π_{c' > c}(1 − u_{c'})`;
  one-way: `P_a · L`.
- **y walk**: a Markov chain over the R+1 height levels, state = the picker's current level,
  starting at ground. Column c is skipped with probability `1 − u_c`; when visited, the entry
  cost is `E|y_cur − y_r|` under the single-visit row marginal `q_r ∝ v_{c,r}`, plus the exact
  expected span inside the column `E[y_high] − E[y_low]` with
  `P(high = j) = v_j·Π_{r>j}(1 − v_r) / u_c` (and symmetrically for low); the next state is
  `q`. This is exact for ≤ 1 visited bin per column and exact-in-span for more (the entry
  side of a multi-visit column uses the marginal rather than the nearer end — third-order at
  the visit densities in play). One-way adds the exit descent `E[y_end]` from the chain's
  final state. Cost: `C·(R+1)²` per aisle, seconds for the whole site.
- **swaps**: renewal count of next-fit over the per-visit volume `v` (units from the bin ×
  `vol_s`): `E[swaps] = V_day / (cap − E[v] + E[v²]/(2E[v]))`. The naive `V_day / cap`
  differs by < 1 % here; both are within ±7 % of the sim.
- **handling**: `Σ_s n·π_s·Σ_k reach_k · M(y_{b_k}) · (intercept + E[u_k]·(per_item + var_s))`.

**Seconds per unit** `E[s] = (x_pace·E[x] + y_pace·E[y] + swap·E[swaps] + E[handling]) ÷ E[units]`,
`E[units] = n·Σ_s π_s·Σ_k E[u_k]`.

**The fixed point** is unchanged from ticket 01: `n·upl·s = K·S·ρ` with `s = s(n)` from above;
`s(n)` is decreasing in n (denser days share aisles), so one scalar root-find (Brent, ~10
evaluations) settles it at setup. Batch content, put crew and receiving crew then derive
exactly as today.

**Put-away** (`put_cost`, from the mouth, no travel between placements) is the simplest
case: per pack `x_b·x_put + y_b·y_put + M(y_b)·(put_intercept + u·put_item + u·var_s)` over
the packs `implied_reorders` already enumerates, with the destination distribution of §4.
Priced against the realised destinations the formula reproduces the sim's put seconds to
**+0.0 %** on both leaves (it is the same function); the class-uniform destination
expectation is within **+2.0 % (store) / +0.2 % (fulfillment)** of the realised destinations'
travel.

## 3. Development-time check against the six passes (no new runs)

Window days 20–39 of the converged fifth pass (`comparison_20260906_131904`, the `_cont`
driver's last), `fifo` arms, `n` = the realised lines/day, `cv` as declared. "Per task x"
and "per visit y" isolate the routing geometry from the demand model.

| leaf, placement model | tasks | per-task x | per-visit y | swaps | handling | units | **s_pick pred / real** |
|---|---|---|---|---|---|---|---|
| store, initial placement | +13.1 % | **+1.1 %** | **+2.0 %** | +6.2 % | +3.3 % | +7.6 % | **103.6 / 106.9 (−3.1 %)** |
| store, class-uniform | +62.8 % | −7.7 % | +10.3 % | +6.2 % | +3.3 % | +7.6 % | 105.6 / 106.9 (−1.1 %, by cancellation) |
| fulfillment, initial placement (one-way) | −12.0 % | **+0.0 %** | −6.4 % | −3.8 % | +3.9 % | +12.4 % | 24.6 / 29.3 (−16 %) |
| fulfillment, class-uniform (one-way) | +8.9 % | **+0.0 %** | **+0.8 %** | −3.8 % | +3.9 % | +12.4 % | **27.3 / 29.3 (−6.8 %)** |

Pass 0 (the capped, stock-starved window) reproduces handling exactly and the store's
per-task x to +2.5 %, but its line→visit mapping is off by 20–160 % because most realised
lines there were partial carries — it is not a test of the formula, and the empirical-rate
variant (`EMP=1`, rates read off the window's own picks) confirms the routing terms alone are
within ±10 % there too.

**What the residual is, and its causes.**

1. **Stock-thinned lines** (+7.6 % / +12.4 % units): even in-band, realised units per line
   are 9.6 / 9.2 against the catalogue's 10.4 / 10.5 because a line that reaches an empty or
   short bin picks less (ticket 09's `unpicked_unstocked`). The formula prices full lines, so
   it under-prices seconds per unit by roughly that share. Shrinks with the coverage fix
   (§5), which is what makes the replenishment cycle fit the day.
2. **Placement drift under FIFO restock** — the finding of this check. Every reorder lands
   on a uniformly random free bin of its class, so a churning section's bins migrate from the
   initial map toward the class-uniform smear. Fulfillment (4,064 reorder placements a day)
   is already there by day 20: the smeared model is right (−6.8 %) and the initial-placement
   model is 16 % low (its tasks −12 %: the initial map concentrates visits on fewer aisles).
   The store (63 placements a day, a ~40-day cycle) has not churned: initial is right
   (−3.1 %) and the smear is absurd (+63 % tasks). Both are the *same formula* with a
   different destination distribution; §4 says which one the era uses where.
3. **Independence across bins** (store tasks +13 % at the right visit count, +10 % under the
   empirical rates): the affinity lift clusters a day's lines, so fewer distinct aisles are
   touched than i.i.d. predicts. Second-order on s_pick (per-task x is right, and the aisle
   count enters only through the per-task terms).

All three push the same way — the formula prices a *cleaner* day than the sim runs — and
all three are inside `band_tol` (0.10) after the coverage fix removes the first. The
equilibrium REPORT is where a formula error shows, as designed.

## 4. Design: one pure module, two destination adapters, one fixed point

**Module** `Optimization/simconfig/expected_travel.py` (pure, imports `cost_model` +
`Aisle_Dimensions` geometry only, like `staffing.py`). Interface:

```
Geometry.from_layout(aisle_rows)            # the warehouse.db / warehouse_meta rows
PlacementDist                                # the seam: bin -> visit-rate contributions
    .initial(bin_map)                        #   the arm's own placement (worker, per arm)
    .uniform(section_units, geometry)        #   class-uniform smear (pair level, no placement)
expected_pick(section, geometry, dist, pick_cfg, n, cv) -> {tasks, x_s, y_s, swaps, handling_s, units, s_pick, ...}
expected_put(packs, geometry, dist, put_cfg) -> {s_put, travel_s, handling_s}
solve_n(section, geometry, dist, pick_cfg, cv, K, S, rho) -> (n, s_pick, detail)
```

`PlacementDist` is the one real seam — two adapters exist because two things genuinely
vary across it (§3 finding 2). Everything else is implementation. Tests price a
three-aisle toy by hand and pin the two identities the sim guarantees (two-way x = x_max,
one-way x = L) plus the put-cost identity (`expected_put` on a single known destination ==
`put_cost`).

**Where each expectation runs.**

- **Pair level, before precompute** (today's stage A in `_derive_staffing_for_pair`): the
  **class-uniform** expectation on the built geometry (`shared['warehouse_meta']`) gives
  `s_pick` per channel and `s_put` for the site, `solve_n` gives the daily demand, and batch
  content follows. This is the era's regime: **one shared script per pair**, arm-independent
  — the paired per-batch analysis and the shared-`_batches` invariant survive. It is also,
  by §3, the FIFO baseline's long-run steady state, so the baseline sits on the band and
  every ranked arm's distance from it is what optimization bought.
- **Per arm, after the initial placement** (the worker, right after `enqueue_all` /
  `_arm_aisle_state`): the **initial-placement** expectation for that arm, stamped as
  `expected_s_pick`, `expected_utilization['pick']` on the arm's `sim_result` staffing block
  with the placement fingerprint. The equilibrium report reads the arm's own expectation
  (04's "per-leaf expected_utilization" becomes per-arm). This costs ~1–2 min per arm at 400k
  SKUs (the rate accumulation is a per-SKU Python loop; the aisle DP is seconds) — beside a
  placement that takes longer. Stamped, not used to size anything.

**The record.** `staffing.derived[<pair>]` gains, per channel, the expectation's components
(tasks/day, x/y seconds, swaps, handling, units, `s_pick`) stamped `derived` with
`geometry_fingerprint` (= `warehouse_fingerprint`) and `placement: uniform`; the per-arm
block adds `placement: initial` + the arm's placement fingerprint. `s_put` likewise.

**What goes away.** `calibration_record.json`, `calibration.py`'s loading/staleness/
`k_max` machinery, `reference.py` + `run_reference.py` (the fixed-point driver), the
`measured` and `seed` provenance for `s_pick`/`s_put`, `travel_share`, `k_max`,
`calibration_stale`. **What stays:** the `--s-pick-*`/`--s-put` overrides as `declared`
constants (a what-if knob, cheap), `PROVENANCE` (all five values still have users),
`equilibrium.py` as the REPORT, `analytic_pick` reduced to a helper (its ground-height,
travel-free prediction is the `dist=None` corner of `expected_pick`). *Reference run* and
*Calibration record* leave the glossary; *Knowability* is amended ("travel is an expectation
over the placement distribution the restock policy induces").

## 5. The coverage denomination — recommendation: runtime rescaling at setup

The catalogue authors `equilibrium_qty = round(cov_batches × freq × qty_rate)`, i.e.
`Q_s ∝ d_s`: coverage in days of a SKU's own demand is ALREADY uniform across SKUs,
`Q_s / d_s = cov_batches × W / n` (W = Σ freq, the lines in one "generation batch"). The
shape is right; only the scale carries the implicit batch, and the scale needs `n`, which is
a *runtime* quantity (it depends on the pickers and the built geometry). So it cannot be a
generator knob without smuggling a day into the profile — the charter's rule.

**Recommend:** the era declares `coverage_days` and `safety_days` as staffing inputs
(`STAFFING_KEYS`, provenance `assumed` → `declared`), and setup re-derives every SKU's
`equilibrium_qty = max(1, round(coverage_days × d_s))` and `reorder_point` by the generator's
own formula with the day as the unit, where `d_s = n·π_s·(λ_s + e^{−λ_s})`. Because the
warehouse is sized from Q and `n` depends on the geometry, this is a fixed point at pair
level — Q(n) → geometry → s(n) → n — but under the class-uniform model each iteration is
seconds (48 store classes, 3 fulfillment), and it converges in 2–3 rounds because `s` moves
weakly with the aisle count. Flag-off is byte-identical (the catalogue's Q stands).

Two things to be aware of. (a) Rounding floors: `Q ≥ 1`, `r ≥ 1` make low-demand SKUs
reorder on their first pick whatever the coverage says — that, not coverage, is why the
store leaf saw reorders from batch 1 while its nominal coverage is ~1,500 days; the
rescaled Q makes the coverage *nominal AND real* only for SKUs with `Q ≥ 2`. (b) The
regenerated stock levels change the warehouse's aisle count, so archive comparability is
gone — it already went with the per-item charge.

## 6. Decisions for the user before the build

1. **Pair-level n from the class-uniform expectation, per-arm expectation stamped** (§4).
   This departs from the ticket's letter ("after the initial placement is known" for the
   demand) to keep one shared script per pair. Alternative: per-arm scripts — breaks the
   paired per-batch analysis.
2. **Coverage as runtime rescaling** under declared `coverage_days` (default? 10?) and
   `safety_days` (§5), with the pair-level fixed point through the warehouse sizing.
3. **Retire `k_max`** with the record (it was a window measurement); the report's pick
   utilization already shows an oversized crew.
4. **Residual policy:** no correction factor; the bands absorb it (all residuals < `band_tol`
   once coverage is fixed) and the report shows the rest.

## 7. Built (2026-09-06, after the four decisions)

- `Optimization/simconfig/expected_travel.py` — the pure module of §4 (`Geometry`,
  `PlacementDist.initial/uniform`, `accumulate`, `routing`, `expected_pick`, `solve_n`,
  `put_site_pricer`); `Tests/unit/test_expected_travel.py` pins the identities. Reproduces the
  check script to the last digit (store pass 5: 103.58 s/unit at 641 lines; `solve_n` lands at
  568 lines/day against the reference run's converged 556).
- `workunits._derive_staffing_for_pair` — stage A is the class-uniform expectation on the
  built geometry and the fixed point; stage B prices put-away with the expected travel;
  the `calibration` block records method / placement / geometry fingerprint / overrides.
- The per-arm stamp: `strategy_runner._arm_expected_pick` after the initial stock, merged
  onto the config's strategy entry by the supervisor, read by the throughput audit through
  `equilibrium.arm_expectations` (the pick band re-centred by the ratio of expected seconds).
- Retired: `calibration.py`, `calibration_record.json`, `reference.py`, `run_reference.py`,
  the `calibration_reference` spec, `--calibration-record`, `k_max`, the stale/measured flags.
- **Not yet built:** the coverage rescaling of §5 (decision 2). It is the next commit on this
  ticket: declared `coverage_days` / `safety_days` on `STAFFING_KEYS`, the pair-level fixed
  point through `plan_warehouse`, flag-off byte-identical.
