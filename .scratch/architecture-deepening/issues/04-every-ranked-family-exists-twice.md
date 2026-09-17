# 04 - every ranked family exists twice, and the dead half feeds the oracles

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17

`Assignment_Functions.py` 2,707 -> 2,325 lines. All four ranked wave impls are thin drivers
over their pools:

| impl | was | now |
|---|---|---|
| `_ranked_assign_impl` | 106 | 53 |
| `_co_demand_ranked_impl` | 98 | 31 |
| `_travel_balanced_impl` | 165 | 43 |
| `_ranked_minlabor_impl` | 190 | 51 |

Each is the expression the ticket names, and it is the expression the equivalence suites were
already running as their third leg:

    return [(u, pool.take(u)[0]) for u in pool.order(units)]

So "is this equivalent?" was answered before the change by the tests' own green: they compare
oracle, impl and pool, and the impl-vs-pool leg was exactly this.

### THE FOG ITEM, resolved -- and the answer differs per family

The ticket asks whether the frozen oracles survive. Three do, untouched:
`_travel_balanced_impl` has its oracle in `test_travel_balanced_equivalence.py`, and
`_co_demand_ranked_impl` / `_ranked_minlabor_impl` have theirs in
`Tests/calltree/test_rank_cache_equivalence.py`, which drives a whole ARM through a frozen
wave wrapped as a pool. Collapsing production's copies reaches none of them.

**`_ranked_assign_impl` was different, and its own test says so**: that suite "compares
against `_ranked_assign_impl` itself as a live oracle". Collapsing it first would have made
the file compare the pool with itself -- the tautology this ticket could have shipped without
noticing. So the body moved VERBATIM into
`Tests/unit/test_ranked_assign_pool_equivalence.py` as `_oracle_ranked_assign_impl` (only the
module-level helpers it reads gained an `af.` prefix), and only then was production's copy
collapsed. The reference is the algorithm that has stood over the pool for its whole life, not
one this refactor authored.

That file already had the instinct in writing, one level down: it translates the pool's
`aisle_key` back into the scan it replaced "so the reference stays the retired algorithm
rather than a paraphrase of the new one". The oracle move is the same rule applied to the
whole function.

### What the suites pin now

`oracle == pool` is the comparison that carries weight. `impl == pool` is true by
construction, and is now the DRAIN CONTRACT rather than an echo: driven in the wave's own
order, the pool makes bit-identical decisions. Proved non-vacuous by perturbing the pool's
`demand_sum` by one part in ten million -- 41 of 51 fail.

### Verification

| check | result |
|---|---|
| the three fast equivalence files + the pool/index suites | 120 passed |
| `test_ranked_assign_pool_equivalence` against the frozen oracle | 51 passed |
| `Tests/calltree/test_rank_cache_equivalence` (pre-merge) | **6 passed in 6m59s** |
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,161 passed / 2 skipped |
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |

The cache suite's runtime is recorded as the ticket asked: 6m59s, inside the 7-13 min band it
was charted at.

### Three things that had to be got right

1. **`_wp_for` stays in three of the four drivers and is absent from the fourth.**
   `_co_demand_ranked_impl` never resolved the regime wp and the other three did; the pool
   does not do it for them. The equivalence tests would NOT have caught dropping it -- they
   pass an already-resolved `wp` with no `by_regime`, so `_wp_for` is a no-op there. Kept
   deliberately, per family, by reading each prologue.
2. **`AisleLedger.over(...)` is an attribute call**, so the pass that prefixed the oracle's
   free names with `af.` missed it -- a `Name(` pattern does not see `Name.attr(`. Caught by
   the tests immediately.
3. `deque` had to join the test file's imports; the frozen body uses it.

### What this unblocks

Tickets 05 (the cold-start tie-break quadratic) and 21 (the pool builders take a ledger rather
than loose dicts). 21 in particular gets easier: the signature change it needs now has one
implementation per family to move instead of two.
