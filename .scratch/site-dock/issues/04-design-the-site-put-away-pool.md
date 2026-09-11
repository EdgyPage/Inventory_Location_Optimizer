# Design the site put-away pool

Type: grilling
Status: open

HITL. Skills: `grilling` + `codebase-design`.

## Question

The charter makes put-away **one site pool over segregated volume**: a putter takes work from either
channel's queue, a cart carries one channel's packs only, and the two leaves share a per-day pool of
crew-seconds rather than a clock. Design the mechanism.

1. **The uid allocator.** `strategy_runner.py:946-1006` (`_put_crew`, `_put_crews`,
   `_bind_put_crews`, `enable_putaway_timing`) builds put crews per arm from `put_crew_spec()` and
   slices them by `_put_crew.workers(uid)` off the pick crew's uid sequence. Two leaves in one
   process each slicing the same site crew **double-book the same workers**. Decide the single
   allocator across both leaves — and check the fifth seam: `workunits._shared` (memory
   `config-knob-has-five-seams`), or the knob silently reverts to its default in every spawned
   worker.

2. **The day budget.** Each leaf's put-away runs as today (`inventory_reorder.py:946-954`,
   `Inventory_Management.py:1383` `_stock`, `:1493` `_stock_per_unit`, `:1821` `_admit`) but the two
   `_stock` calls draw one pool of crew-seconds and one `put_deadline`. Decide the allocation rule:
   what stops the leaf that runs first from consuming the whole day's budget, and what a leaf sees
   when the pool is exhausted (the same cut it sees today, or a new state?). The memory
   `cut-is-a-level-not-a-flow` is the trap: whatever this reports, a level cannot be summed over
   batches.

3. **The source stamp.** `_admit` stamps `source` from `getattr(self.transit, 'SOURCE', ...)`
   (`inventory_reorder.py:481`) — under a shared transit both leaves read the same object. Confirm
   what `source` means on a coupled run and whether the carryover table's key still separates its
   two producers (memory `carryover-two-producers-one-key`).

4. **The put price.** `put_deadline` is a START gate, not a duration (memory
   `working-day-clock-plan-corrections`). Confirm the shared budget composes with that, and with the
   per-channel put price (department-calibration 26) — the site pool is one crew, but the two
   channels' per-unit put costs differ.

5. **What flag-off keeps.** Byte-identical: an inbound-off or single-channel run keeps today's
   per-leaf crew, double count and all. State the switch explicitly so the equivalence test has
   something to assert (CLAUDE.md §2: a new feature must be a strict no-op when its flag is off).

6. **What the band reads.** `equilibrium.py:390-397` already compares a leaf's put load to a site
   crew, and `expected_utilization`'s "single-channel leaves undercut ρ" caveat
   (`staffing.py:663-667`) exists precisely because of the double count. Decide what the put clause
   reads on a coupled run — ticket 07 owns the report, this ticket owns the number it reads.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§1, §4.
