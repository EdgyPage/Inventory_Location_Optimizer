# 07 - helpers and leases: a unit borrows the pool's idle workers for its drain

Type: task
Status: open
Blocked by: 04

CONDITIONAL. Built only if, after ticket 04, a campaign-shaped run's sim-phase occupancy
still falls below 90% (Q7, read off the `[pool]` lines from first submit to last `done`).
The finished run read 8.1 h against a 5.1 h floor with the queue empty from 01:30 and the
last five hours at 14 -> 2 of 16 workers; a 10x cheaper unit may leave nothing to spread
(Q12). Ticket 02's replica RSS decides whether it CAN be built: 16 workers at ~4 GB already
hold 64 GB of 128.

## What the seam is, and what it is not

A coupled unit cannot split by leaf (the site dock is shared; `_plan_strategy_start` refuses
a torn pair by design) or by batch (each site day depends on the last). The only parallelism
inside a unit is inside a drain: `plan_order`'s now-side sweep evaluates every remaining
candidate against the same frozen state, and the argmax over them is order-independent once
the reduction is ordered. Threads buy nothing (`_aisle_best` is Python-level, GIL-bound).

## Design (Q11, elastic)

- **Helper**: a process a running unit owns for its life, holding a REPLICA of the frozen
  tier and the cost-model tables (not the sim), keyed by ticket 04's stable bin key. Spawned
  once per unit at unit start -- a spawn per drain is a ~1 min catalogue load, unaffordable
  -- and idle until leased.
- **Lease**: the pool's grant of idle slots to a running unit for one drain. `WorkPool`
  publishes its idle count through the Manager it already owns; a unit asks for k at each
  drain and returns them after. NEVER while jobs are queued: a queued unit outranks a
  helper. A helper that dies falls the drain back to serial and returns the lease; a unit
  that dies returns its leases when its future resolves. The pool stays parent-thread-only:
  the lease count is the one shared value, and it is a hint, not a lock.
- **Determinism.** The parent advances every replica by one trailer's takes per round (the
  `taken` delta and the round's `B`), each helper evaluates its share of candidates, the
  parent reduces in CANDIDATE INDEX order with the same strict `>`, so the plan is identical
  at any lease count. Toy digest IDENTICAL at lease counts 0 and 4 (Q5).
- The words "helper" and "lease" live in `workpool.py`'s docstring and here; they are harness
  words, not site words, and stay out of CONTEXT.md.

## Bar

Occupancy >= 90% on a campaign-shaped run; the digest identical at 0 and 4 leases; a
`test_work_pool.py` twin for the lease path (a lease granted while jobs are queued is the
regression, silent in the healthy direction); the parent's RSS line reads what it costs.
