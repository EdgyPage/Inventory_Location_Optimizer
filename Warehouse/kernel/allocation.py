"""allocation — split a fixed set of work across N workers.

Extracted from `Pick.assign_tasks`, which was and remains the ONE task→picker partition
both pick simulations call, so they cannot drift.  What is here is the part that has
nothing to do with picking: given some items, a cost for each, and a tie-break order, hand
them out to N workers under a named policy.

A second work stream — an inbound crew unloading trailers — needs exactly this and nothing
else from the scheduler.  Keeping the policies in one place means round-robin and LPT get
implemented once rather than reimplemented next to the dock, and it means the two streams
cannot quietly disagree about what "balanced" means.

`Warehouse/kernel/` may import NOTHING (see `architecture.yml`'s `wh_kernel -> *` forbid),
which is exactly why this module is a good home: both the domain engine and any future
inbound package can reach it, and it can reach neither.

## The two policies, and why LPT balances a proxy

`round_robin` is `i % n` — the legacy assignment, byte-identical, and the default.

`lpt` visits items HEAVIEST-FIRST and appends each to the LEAST-LOADED worker.  That is a
standard makespan heuristic; it is NOT a guaranteed bound on a realized step-function
makespan and on adverse inputs can do no better than round-robin.  It never yields an
invalid partition and total work is unchanged, so results stay correct either way.

The caller supplies `cost_of`, and in the pick case it is deliberately a *continuous* proxy
rather than the exact cost: a cart-swap step function can be fooled by a light item that
happens to look free on a cart-favourable worker.  Balancing on a smooth monotone
approximation and then MEASURING the real thing is the split that keeps the scheduler
honest — see `Pick.count_cart_swaps`, the exact-makespan predictor the proxy is checked
against.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

#: The policies `partition` knows.  A caller passing anything else gets an error rather
#: than a silent fall-through to round-robin — the retired code used
#: `!= 'lpt' → round_robin`, which turned a typo into a quietly different experiment.
POLICIES = ('round_robin', 'lpt')


def partition(items: Iterable[Any], n: int, *,
              policy: str = 'round_robin',
              cost_of: Callable[[Any], float] | None = None,
              order_key: Callable[[Any], Any] | None = None) -> list[list]:
    """Split `items` across `n` workers and return one list per worker.

    `items`      must already be in the order the consumer will process them; the
                 round-robin policy preserves exactly that order within each worker.
    `cost_of`    per-item balancing cost.  Required by `lpt`, unused by `round_robin`.
    `order_key`  the sort key each worker's list is restored to after LPT reorders it,
                 AND the tie-break among equal costs.  Required by `lpt`.

    The tie-break is subtle and load-bearing: LPT sorts on `(cost, order_key)` with
    `reverse=True`, so items of equal cost are visited in DESCENDING key order.  That is
    what the retired implementation did, and changing it silently repartitions every run.
    """
    if policy not in POLICIES:
        raise ValueError(f'unknown partition policy {policy!r} (known: {POLICIES})')
    items = list(items)
    buckets: list[list] = [[] for _ in range(n)]

    if policy != 'lpt' or n <= 1:
        for i, item in enumerate(items):
            buckets[i % n].append(item)
        return buckets

    if cost_of is None or order_key is None:
        raise ValueError("policy 'lpt' needs both cost_of and order_key")

    # id-keyed because an item need not be hashable, and every item is alive for the whole
    # call, so the key cannot be recycled underneath the lookup.
    est = {id(item): cost_of(item) for item in items}
    load = [0.0] * n
    for item in sorted(items, key=lambda it: (est[id(it)], order_key(it)), reverse=True):
        w = min(range(n), key=lambda i: (load[i], i))   # least-loaded; tie ⇒ lowest index
        load[w] += est[id(item)]
        buckets[w].append(item)
    for bucket in buckets:                              # restore the consumer's own order
        bucket.sort(key=order_key)
    return buckets
