# Seat the put-pool injection seams

Type: task
Status: open

AFK. The execution override (map Notes) graduating two byte-identical precursors out of
[Design the site put-away pool](04-design-the-site-put-away-pool.md). No decision is open here;
both are settled in that ticket and this one adds none.

**Both are byte-identical today** — nothing yet passes a clock list to `bind_crew`, and nothing yet
drains a queue twice in one batch. Each becomes a silent wrong answer the moment coupling lands,
which is why they go in ahead of the pool itself. Same shape as
[Harden the three positional seams](11-harden-the-positional-seams.md).

## Question

1. **The clock-injection seam.** `PutQueue.bind_crew(speed, cost, size)`
   (`Warehouse/inventory/put_queue.py:224`) builds its own clocks via
   `crew_clock.new_clocks(size, self.name)`. Give it an optional pre-built `clocks` list, threaded
   from `Inventory_Manager._bind_put_crews` (`Inventory_Management.py:1701-1714`) and
   `enable_putaway_timing` (`:1112-1139`). When absent — every caller today — the behaviour is
   exactly what it is now.

   The companion: `drain_putaway_records()` (`:1310-1335`) must be able to hand over the records
   **without** resetting the queues' clocks, because on a coupled run the pool owns the reset (04
   section 1) and a leaf resetting a shared list mid-day is a silent zeroing. Default stays "reset",
   so nothing moves.

2. **The cut count, extracted.** `_stock` charges `queue.cut += len(queue.items)` for every queue
   that could not start, in a loop at the end of the call
   (`Inventory_Management.py:1488-1491`). Extract it as a public `count_put_cut(deadline)` and have
   `_stock` call it inline exactly where the loop sits now. The coupled pool will call it once per
   leaf after its residue pass instead, so the cut is charged once per site day rather than once per
   pass — `cut` is a LEVEL (memory `cut-is-a-level-not-a-flow`) and a double charge inside one batch
   is not recoverable by any "count the non-zero batches" rule downstream.

**The gate:** `python -m pytest Tests/unit -q` plus the put-away day-cut integration test
(`Tests/integration/test_putaway_day_cut.py`) and `Tests/unit/test_crew_clock.py`, which are the
two files that already exercise `bind_crew` directly. A diff of `work_events` and
`put_queue_state` rows against a pre-change run must be empty.
