# S00 -- anchors: where a full-scale run's wall goes today (existing roots, read-only)

## Question

Before instrumenting anything: of a 400k run's wall, how much is parent setup, the
simulate stage, the analyse stage -- and how close is the simulate stage to its bound
max(slowest unit, sum of units / workers)?

## Prediction (registered in the plan before measuring)

Simulate stage >= 1.6x its bound; analysis + parent setup >= 25% of the campaign wall.

## Measurement (`assets/s00_stages.py`, run.log timestamps + runtime_metrics)

### Per-arm sections (runtime_metrics `runtime`, means over arms)

| root | total_s | reord | pre | save | extract | kf | sim | unnamed |
|---|---|---|---|---|---|---|---|---|
| campaign `comparison_whatif_20260920_150203`, 40 batches, 11 cells | 6,651 s | **88.2%** | 2.7% | 0.5% | 0.7% | 0.6% | 0.1% | ~6% |
| churn probe `_20260923_175345`, 40 batches, fifo/lifo | 1,419 s | 40.5% | 11.5% | 3.9% | 3.9% | 2.5% | 1.4% | **~34%** |

Campaign by cell (mean per arm; `reord_s`):

| cell | total | reord | other named | unnamed |
|---|---|---|---|---|
| inb_off | 1,128 | 205 | 316 | 607 |
| fifo / lifo | 1,176 / 1,186 | 375 / 375 | 314 / 320 | 487 / 491 |
| ggated_h100 | 1,724 | 908 | 323 | 493 |
| gmyopic_k8 | 3,098 | 2,289 | 318 | 491 |
| gmyopic | 7,421 | 6,690 | 297 | 434 |
| ggated_h050 / h025 | 6,690 / 9,735 | 5,854 / 8,933 | ~325 | ~490 |
| gforecast | 13,634 | 12,981 | 266 | 388 |
| fsight_w5 / wall | 13,820 / 13,550 | 13,101 / 12,797 | ~293 | ~443 |

By arm: the winner pair's `reord_s` averages 10,100-12,500 s (max 27,757 s = 7.7 h); the
fifo rider's 408 s.  **Inbound under fifo costs ~170 s per arm over inb_off** (375 vs 205).

### Stage walls

| | campaign 20260920 | churn probe 20260923_175345 (single cell) |
|---|---|---|
| run wall | 15.89 h | 1.12 h |
| parent setup before the first unit | 0.35 h | **0.39 h (35% of the wall)** |
| simulate stage | 15.4 h over THREE launches (resumed 20:52 and 22:37) | -- |
| last launch, 22:37 -> 06:45 | **8.1 h against a 7.9 h slowest unit (1.03x)** | -- |
| analyse stage | 0.17 h | (`--no-analyze`) |

The campaign's 15.9 h is two interruptions, not dispatch: in the final launch the simulate
stage sat within 3% of its slowest unit.  The critical path IS the slowest unit, and 97%
of that unit is `reord_s` -- the gain planner pricing the winner pair through the pool
adapter.

### The single-cell parent setup, second by second (churn probe, 400k)

| step | seconds |
|---|---|
| catalogue load | 4 |
| coverage: line floor SOLVED, store / fulfillment | 206 / 197 (17 evaluations each) |
| fill derived per bucket + staffing | 29 + 54 |
| coverage: expected first-pass fill, store / fulfillment | **300 / 320** |
| warehouse planned ("Warehouse ... (1129.6 s)") | total 1,130 |
| per cell: shape-only rebuild, staffing, batch precompute x2, put crew | ~230 |

Workers then pay per leaf: catalogue 9-10 s, warehouse 4-5 s, initial stock 57-74 s.

## Residual / Diagnosis

* **Prediction 1 (simulate >= 1.6x bound): refuted** for the last launch (1.03x).  O0
  (dispatch order) has nothing to win at campaign scale.  Dropped.
* **Prediction 2 (analysis + setup >= 25%): refuted for the campaign** (0.35 + 0.17 h of 15.9
  h), **confirmed for a single-cell research run**: 35% of a 400k probe's wall is parent
  setup, and 1,130 s of it is the coverage/staffing closed forms -- ~25 evaluations of
  `coverage.fill_rate` over ~240k SKUs at ~12 s each, a per-SKU Python loop
  (`Optimization/simconfig/coverage.py:437-517`) whose per-SKU terms
  (`line.expected_min(q)`, the lead-served term) depend on a small number of distinct
  (line law, level) pairs.  New optimisation **O9**: memoise those terms per distinct key,
  same summation order -- exact.
* The campaign's unnamed ~390-600 s per arm and the probe's 34% are the per-leaf setup and
  the sibling leaf's setup inside `total_s` (strategy_runner builds the two leaves one after
  the other) -- I0's `startup_s` / `sib_setup_s` will split it.

## Next

S01: I0, the inbound columns.  Then run A.
