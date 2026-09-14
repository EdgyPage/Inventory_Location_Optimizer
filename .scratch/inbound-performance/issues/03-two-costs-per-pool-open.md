# _make_pool pays TWO O(warehouse) costs per virtual placement, not one

Type: research
Status: open

Found by reading, 2026-09-13, while the instrument was still being built. Recorded now because it
materially changes the design of the effort's headline refactor, and because it is falsifiable by the
ladder once that exists.

## Question

`Inbound/gain.py:929-938` `_Evaluator._make_pool` is the named #1 suspect — the
`inbound-optimization` map already flagged it ("`_make_pool` rebuilds the policy per virtual
placement and `plan_order` is O(yard^2) of those per drain"). The planned fix was a copy-on-write
overlay replacing the eager `AISLE_COPIERS` copy.

Is the copy the whole cost?

## Finding: no. There are two, and the second survives the planned fix.

### Cost 1 — the eager aisle-dict copy

```python
state = {n: AISLE_COPIERS[n](d) for n, d in b.aisle_state.items()}   # gain.py:937
return b.pool_factory(list(cands), state, wp)                        # gain.py:938
```

`_copy_of_sets` walks every aisle and rebuilds each SKU set; `_copy_of_lists_by_key` (the
`rank_minlabor` shape) is dict -> dict -> list, two levels deep. O(total SKU-aisle memberships),
which is O(occupied bins).

### Cost 2 — the pool's own `__init__` unions the whole warehouse

Inside the pool the factory then opens:

```python
self._all_idx = (set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set())
```

`Warehouse/placement/Assignment_Functions.py:893` (`_TravelBalancedPool`-family) and `:1109`
(`_RankedAssignPool`, guarded by `order_key is None`). Also at `:642`, `:765`, `:2686`.

This is a **second** O(occupied bins) pass, in `__init__`, therefore **per pool open** — and a
copy-on-write overlay does not remove it. Worse, a CoW overlay must still expose `.values()` across
the entire live key space for this union to be correct, so the overlay cannot be lazy about the
very access that dominates.

The authors already optimized this *within* a group and said so at `:1106-1108`:

> The co-occurrence term ranks each SKU against ALL currently-placed SKU indices. That union is
> identical for every unit in the group, so build it ONCE -- not once per unit inside the sort key
> (which was O(U*sigma) per wave).

That reasoning stops at the group boundary. Nothing hoists it across **pool opens**, because in the
production restock path a pool is opened once per wave and the cost amortizes. The inbound gain
evaluator breaks that assumption: it opens a pool per tier per virtual placement, inside an O(T^2)
greedy, twice per drain.

## Consequence for the refactor

The fix must address both, or the measured win will be roughly half of what the mechanism hypothesis
predicts — and per this effort's own rule, a post-fix exponent that does not move is the refutation.

`_all_idx` is a pure function of `aisle_idx_sets`, and the evaluator knows exactly when a virtual
placement commits a new index to an aisle, so caching it on the evaluator and refreshing winner-only
is the natural shape. That is the same pattern as the Phase-6 SKU-run caches — and carries the same
recorded hazard: **value-correct is not byte-identical.** A cache held across a set's growth once
flipped a summation order and drifted one ulp, moving a placement. `set().union(...)` over a growing
set is exactly that shape.

## Open questions

- Which `PHASE2_PAIRS` arms actually reach `:1109`? It is guarded by `order_key is None`, and
  `rank_random` passes an `aisle_selector`. Determine per arm which of the five pool families pays
  cost 2 and which pays only cost 1.
- Is cost 2 larger or smaller than cost 1 at production scale? They are the same order; the constants
  decide, and only a capture answers it.
- Does the eager copy of `aisle_idx_sets` (cost 1) exist ONLY to feed this union? If so the two
  collapse into one problem with one fix.

## Answer to the first open question, and a NARROWING of the finding above

Traced each `PHASE2_PAIRS` arm to its pool class and checked whether that class builds `_all_idx`:

| arm | factory -> pool | builds `_all_idx`? | why |
|---|---|---|---|
| `rank_labor` | `build_ranked_labor_pool_fn` -> `_TravelBalancedPool` (:1533) | **no** | no `_all_idx` anywhere in the class body |
| `rank_cartlabor` | `build_ranked_cartlabor_pool_fn` -> `_TravelBalancedPool` | **no** | same |
| `rank_popularity` | `build_ranked_popularity_pool_fn` (:1346) -> `_RankedAssignPool` | **no** | passes `order_key=_score_expected_popularity`, and :1109 is guarded by `order_key is None` |
| `rank_random` | `_RankedAssignPool` direct (`strategy_runner.py:288`) | **YES** | no `order_key` passed -> defaults None -> :1109 fires |
| `rank_minlabor` | `build_ranked_minlabor_pool_fn` (:2328) -> `_build_minlabor_pool_fn` | **no** | its pool is not `_RankedAssignPool`; :2686 belongs to `build_cluster_map_placement` (:2584), a different arm |
| `tmin` | merge adapter | n/a | opens no pool |
| `fifo` | uniform adapter | n/a | opens no pool |

**So cost 2's blast radius among phase-2 arms is ONE arm — `rank_random`, store side, pair 5 — not
most of them.** The three other `_all_idx` sites (`:893` `_CoDemandPool`, `:2686` cluster_map,
`:642`/`:765` the non-pool assignment fns) belong to arms phase 2 does not run.

The finding above is **narrowed, not retracted**: cost 2 is real, is per pool open, and would survive
a copy-on-write fix — but it is a one-arm problem, so it must not be used to justify complicating the
cost-1 fix for every arm. `rank_minlabor` — the arm that motivated the concern, being fulfillment's #1
— pays only the `_copy_of_lists_by_key` two-level copy, which is exactly what copy-on-write addresses.

Corrected 2026-09-13, same session, before the ladder existed to check it. Recording the wrong version
above rather than deleting it, because "which arms pay this" is the question a future reader will ask
again, and the answer is not visible from the call site.
