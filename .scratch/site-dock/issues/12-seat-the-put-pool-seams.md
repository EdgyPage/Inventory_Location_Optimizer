# Seat the put-pool injection seams

Type: task
Status: resolved

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

## Answer

**BUILT and live on `develop`** — `564f7e27` (the seams and their tests), `248ebfcc` (the
architecture layer). Both are byte-identical, proven by two full runs rather than argued.

### 1. The clock-injection seam

`PutQueue.bind_crew(speed, cost, size, clocks=None)` takes a **pre-built worker list**
instead of minting one. `crew_clock` is functions over a bare `list[float]` and `reset`
mutates in place, so sharing is **identity**: two queues handed the same list book against
the same people, and a putter busy on one stream is busy on the other. `clocks=None` —
every caller today — is the method's whole history, unchanged.

Threaded `enable_putaway_timing(..., clocks=...)` → `_bind_put_crews` → `bind_crew`, and
the list is **held on the manager**, not passed through once. That is not tidiness: the
queue set can be replaced after the bind (`put_queues` setter → `_bind_put_crews`, which
`test_swapping_the_queue_set_keeps_timing_bound` already exists to protect), and a rebind
that minted fresh clocks would drop the pool on the floor with nothing raising.

`drain_putaway_records(reset_clocks=True)` hands the records over and, with `False`, leaves
the crew standing. `_put_clock` moves with the flag, not beside it — it is `max(q.finish)`
by construction, so zeroing it while the clocks run on would leave the manager's own view
of the put side disagreeing with the crew it is read from. (It is write-only today; that is
a reason to keep it honest, not a reason to let it drift.)

### 2. The cut count, extracted

`_stock`'s tail loop is now a public `count_put_cut(deadline)`, called inline exactly where
it sat, with the three paragraphs that justify it moved onto the method. `deadline is None`
charges nothing, so the pool can call it unconditionally at the end of its day.

### 3. Two deviations from the ticket, both under force

- **`_stock` gains `charge_cut: bool = True`.** As written the ticket produces a *half*
  seam: a public `count_put_cut` with no way to stop `_stock` charging cannot be called
  *instead*, which is what 04 section 4 asks for. The pool's two drains would each charge,
  and because `cut` is a LEVEL the second re-counts what the first already did — inside one
  batch, where no downstream "count the non-zero batches" rule can undo it. The pool's shape
  is therefore `_stock(deadline=sub, charge_cut=False)` twice, then one
  `count_put_cut(full_day)`. `test_charging_at_both_passes_would_inflate_the_level` asserts
  the inflation the flag prevents, so the flag is not merely available but load-bearing.
- **A queue naming its own `spec.crew` REFUSES an injected pool**, and so does a declared
  `size` that contradicts the injected list. An injected list is the manager's **default**
  crew; refusing rather than applying it to the subset, because a run where some streams
  share the pool and others field their own people is neither the pooled model nor today's,
  with nothing on any row telling them apart. The size refusal is the same argument one
  level down: `crew_size` reads the LIST, so keeping the list and ignoring the number would
  put every utilization denominator downstream out of step with the staffing record that
  sized the crew, and both numbers are plausible.

### 4. Findings

- **04's "hand both managers' queues the same list" is well-posed only while
  `put_queue_split` is off — and it always is on a coupled run, for a reason that already
  existed.** Production really does field three per-queue crews when the split is on
  (`strategy_runner.py:965-973` passes `cart_crew`/`pallet_crew`/`ff_crew` explicitly), so
  a site pool and a split queue set are two different answers to "who are the putters".
  That looked like an open decision for the pool build. It is not:
  `workunits.refuse_unpriceable_put` **already refuses `put_queue_split` outright** for the
  era derivation, and `run_simulation._check_era_flags` refuses it again at the parser — so
  no run that derives its crews can have the split on, and every coupled run derives.
  The refusal added here is the same incompatibility stated a third time, at the manager,
  where the pool is actually bound. No ticket; nothing is owed the pool build but the
  knowledge that the refusal is unreachable by design rather than by luck.
- **`_put_clocks` was already taken — by the defect this file fixed.** `_bind_put_crews`'
  own docstring ends "which is exactly what a single manager-level `_put_clocks` did",
  naming one list that serialised every stream onto one clock. The pool is that same shape
  *deliberately*, and across two managers. Two opposite meanings three lines apart is a
  reader's trap, so the held list is `_put_pool_clocks`.
- **`PutQueueSet.snapshot()` is destructive** — it is `[q.drain_counters() for q in ...]`,
  so the second call on one manager reads zeros. Nothing says so at the call site, and the
  name says the opposite. It cost a test that read `_cut(mgr)` twice.

### 5. The gate

- `Tests/unit` **2,023 passed** (8 new); `Tests/integration` **406 passed, 1 skipped** (3
  new); the two files the ticket names green throughout.
- **Byte-identity, measured.** Both preflight canaries run against a `git archive HEAD`
  copy and against this tree: identical tree shape, and `work_events` (5,136 + 3,076 rows,
  **668 of them puts**) and `put_queue_state` diff **row-for-row to zero**. Canary B was
  re-run after the last change, so the proof covers the tree as committed. The put rows were
  counted before trusting the diff — an empty table diffs clean.
- **Every new guard mutation-checked: six mutations, six caught** (the size refusal, the
  half-applied refusal, a copied instead of shared list, a drain that resets regardless, a
  pool not held on the manager, an ignored `charge_cut`).
- `arch` + `site` + `context` + `contract` + `preflight` + `profile-tree` + `memory` +
  `path_guard` + `docref_guard` all OK. The architecture layer was stale after the change
  and was regenerated through the full chain; `arch-synced-commit` moves to `564f7e27`. No
  schema fingerprint moved — this touches no DDL and no run-tree shape.
