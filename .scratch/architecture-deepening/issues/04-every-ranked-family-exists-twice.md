# 04 - every ranked family exists twice, and the dead half feeds the oracles

Type: refactor
Status: needs-triage
Blocked by: 03

## Context

Each ranked family has two production implementations of the same scoring rule:

| wave form | pool form |
|---|---|
| `_ranked_assign_impl` `:601-710` | `_RankedAssignPool` `:1064-1235` |
| `_co_demand_ranked_impl` `:751-851` | `_CoDemandPool` `:852-987` |
| `_travel_balanced_impl` `:1423-1585` | `_TravelBalancedPool` `:1588-1847` |
| `_ranked_minlabor_impl` `:1976-2167` | `_MinLaborPool` `:2169-2377` |

`_cart_cost`, `_aisle_best`, `_score_of` are defined twice inside the travel family alone
(`:1487/1491/1504` as closures, `:1724/1728/1745` as methods); `_better` and `_aisle_best_cost`
likewise at `:2023/2056` and `:2247/2250`.

`Warehouse/inventory/Inventory_Management.py:2100-2107` states the position:

> LEGACY. No shipped restock rule reaches here any more: 14 are pooled and the other 3 (fifo,
> cmax, cmin) have no group path at all. Kept because `place_wave` is still how the frozen oracles
> are driven in the equivalence suites, and deleting it would delete the thing the ports are
> checked against.

Eleven builders (`build_ranked_*`, `build_optmap_wave_fn`, `build_load_*`) have zero non-test
callers. That is roughly 900 lines reachable in production only as a test oracle, plus
`Placement.place_wave` -- a third slot on the dispatch interface with no production adapter.

**The cost is already being paid.** The perf round deepened only the pool copies -- heap selection
at `:1199`/`:1816`, the fused scan in `_cluster_map_choose_aisle`, copy-on-write views -- and every
one had to be ARGUED byte-identical against a hand-maintained twin. The comments at `:1561`
("mirrors `_ranked_assign_impl`") and `:1820` ("mirrors `_RankedAssignPool`") are load-bearing
prose where a call should be.

## What to build

Make the `_impl` functions thin drivers over the pool rather than second implementations:

```
_ranked_assign_impl(units, ...) = [(u, pool.take(u)[0]) for u in pool.order(units)]
```

The equivalence suites keep running and gain meaning -- they would then pin the **drain contract**
(pool-driven-in-order == wave) rather than an echo. The per-family scoring math gets one home.

**Deletion test:** deleting the `_impl` bodies CONCENTRATES -- the scoring rule ends up in one
place and the tests get stronger.

## Fog to resolve inside this ticket

Whether the three frozen oracles in `Tests/calltree/test_rank_cache_equivalence.py` survive. They
are pinned to commits `831571f` and `c2fcc2b` and `_PATCHED` at `:393` monkey-patches module
globals -- which the file itself warns at `:346` can silently stop reaching the code under test.
If the collapse means the oracles must be re-frozen, that is a decision to record, not to make
quietly. That suite is 7-13 min and pre-merge only, deliberately outside the ten gates.

## Verification

- The three ~4 s equivalence files, exact-float.
- `test_rank_cache_equivalence.py` once, pre-merge, with its runtime recorded.
- `run_digest.py` DB-row neutrality.
- Gates 1, 2, 10.
