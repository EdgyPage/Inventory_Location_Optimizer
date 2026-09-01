# Build total production hours

Type: task
Status: open
Blocked by: 08

## Question

Make the objective the effort has been reasoning about actually reportable. Ticket 10 fixed
the selection metric as TOTAL PRODUCTION HOURS = unload + put + pick, and 08 made it phase
1's selection metric — but **no artifact reports it today, and one of its three legs is not
persisted as a scalar at all.** Selecting on what exists instead (pick hours) would
systematically favour arms that buy pick time with put-away time, which is the exact trade
this effort exists to measure.

Where the three legs stand:

| leg | where it lives | reported today |
|---|---|---|
| pick | `task_stats.duration` summed per batch | yes — the `production_time` quantity |
| unload | `batch_stats.recv_seconds` | a table column with **zero quantities declared** |
| put | **not in `batch_stats` at all** — only `work_events.duration WHERE role='put'` | no |

- **The unload leg** is the straightforward half: a new declared Quantity over a column
  already in the frame. `recv_seconds` is declared FLOW/s/batch in `sim_semantics.py` and
  is deliberately disjoint from `putaway_seconds` — do not merge them.
- **The put leg is the real work.** `putaway_seconds` exists only as an in-sim property on
  `Inventory_Management` and is never written to `batch_stats`. The hours live in
  `work_events`, which **the analysis suite has never read a single row of** — so this
  needs a new frame kind, not just a new quantity.
- **The crew discriminator is `role`, not `queue`.** `work_events.role` is
  `'pick' | 'put' | 'receive'`; there is no `queue` column on that table (`queue` belongs to
  `put_queue_state` and `reorder_queue`). The wording in 08's early comments was loose about
  this. Per-crew hours are `SUM(duration) GROUP BY role`.
- **`work_events.duration` is nullable BY DESIGN** — an interval for put/receive, NULL for a
  pick row (an instant). It was `NOT NULL DEFAULT 0` until 2026-08-25, and the DDL comment
  records that `SUM(duration)` "silently returned put+receive labour only while looking like
  a total". Any aggregate here must state its NULL handling explicitly.
- Follow the route discipline in the `route-reviewer-finding` skill: the unload leg is a
  clean R3; the put leg is R3-with-a-new-frame-kind.
- Order is committed evidence: `_METRICS` order is the row order of every significance CSV,
  and `AGGREGATE_ORDER`/`HEADLINE_ORDER`/`SERIES_ORDER` are validated at import — **new
  quantities go at the END.**
- 08 also granted `yard_overage_days` a `headline` slot beside hours and missed share. That
  quantity belongs to [Build the yard metrics](17-build-the-yard-metrics.md); the
  `HEADLINE_ORDER` entries from both tickets need to land without fighting each other.

Honesty note inherited from 01: total unload hours vary across arms only through the
reorder feedback loop, so the first-order lever is placement quality buying put + pick
hours. Inbound-off (phase 1) the unload leg is near-absent, which means phase 1's metric is
effectively **put + pick** — and the put leg is exactly the piece that does not exist yet.
