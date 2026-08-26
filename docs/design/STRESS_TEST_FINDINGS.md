# Stress-test findings — put-away queues, the working-day clock, the receiving dock

The machinery shipped in `dd68e00..fa18639` was correct at 4–8 batches and had never run at
scale. This is what running it at scale found: two halves, **growth** (does anything get
super-linearly worse) and **correctness** (does the carry close).

Reproduce with `Tests/calltree/calltree_growth.py` for the ladders and
`Diagnostics/receiving_report.py` for the reconciliation. Neither is in a gate — see section 5.

---

## 1. Growth

### The instrument had to be re-aimed first

The plan proposed a `batches` ladder at 40/80/160/320. **That knob cannot find growth in this
subsystem**, and the reason is structural rather than a tuning problem: every backlog *level*
saturates on it. Over 20 → 160 batches the dock went only **4,923 → 6,471**. Anything upstream of
a bin is already credited to `_queued_qty`, which suppresses further reorders, so the backlog
reaches a fixed point however many batches are added. A saturating quantity fits k ≈ 0 and reads
as "no growth found".

The risk lives on the **skus** knob, and that is where the ladders below were run.

### S1 — `_admit_held()`: CONFIRMED, quadratic, fixed

The one real defect. `_stock`'s refill loop calls `_admit_held` once per pass and needs roughly
`work / staging` passes, so passes and the held list grow together. Without an early break the
retry re-walked every remaining held item on every pass — a `PutQueueSet.route()` each time — to
reach a conclusion the first blocked-out queue had already settled.

At 40 batches, `staging=4`, exact call counts:

| SKUs | 300 | 2,400 | exponent |
|---|---|---|---|
| before | 677,845 | 27,248,644 | k = 1.784 |
| after | 42,736 | 283,774 | k = 0.914 |
| unstaged (control) | 26,408 | 174,738 | k = 0.912 |

**96x fewer calls**; wall 41.7 s → 21.7 s. Fixed in `68bf962`, pinned by
`Tests/unit/test_admit_held_early_exit.py` — which asserts the call **count** (exact and
deterministic, where a stopwatch would be flaky) *and* separately that the early exit matches an
exhaustive walk item-for-item and in order, because an exit that dropped or reordered an item
would be worse than the slowness it cured.

**Why it survived every prior test:** with `staging=None` the held list is always empty and the
refill loop runs exactly one pass. The defect was unreachable until the split put-queue
configuration became selectable with real staging limits (`53bee99`).

### The cost is staging, not the split — and after the fix it is a constant

A 2x2 decomposition, because "split queues are slow" and "backpressure is slow" are different
findings with different remedies:

| configuration | exponent |
|---|---|
| baseline | k = 1.100 |
| + split queues, no staging | k = 1.013 |
| + staging, single queue | k = **1.450** |

The split costs nothing. **Staging carried all of it.** After the S1 fix, baseline k = 1.238
against staging k = 1.281 — the exponents match, and what remains is a **2.6x constant factor**,
not a growth term. A constant factor is a budgeting question; a growth term is a defect. This is
now the former.

### S2–S8: refuted or constant-factor

None of the other seven produced a super-linear exponent under the ladder. `_put_clock` (S6),
`_windowed` at `k_cap=1` (S7) and `_MultiQueueView.__iter__` (S8) are wasted **constants** — real
but bounded, and S8 only on the diagnostic path. The level-walkers (S2 `queue_contents`,
S3 `carryover_rows`) track the backlog, which the saturation above bounds.

These were fitted as a group against the ladder rather than isolated one at a time. A
per-suspect attribution would need its own decomposition, and none showed an exponent worth the
run.

---

## 2. Correctness — the 200-batch rollover run

The real `_run_strategy_worker_impl` loop, 200 batches, single store arm, **all three carries on
and all three starved**: `work_day_seconds = 2000`, `recv_day_seconds = 30` with a crew of one,
`put_*_staging = 8`. Every prior rollover test was synthetic, ≤ 10 batches, or a
reimplementation of the runner loop.

### All four invariants HOLD

1. **Demand closes.** `picked + carried + unmet + shortfall == sum(_eff_batch.items)` in
   **200 of 200** batches. This is the assertion R3 predicted would fail.
2. **Unit conservation closes** across `_queued_qty`, `queue_depth`, `dock_depth`, `_held`,
   `in_transit_qty`, `lead_queue_depth`.
3. **No crew stalls** (R5, refuted below).
4. `items_demanded` grows at k = 0.976 — linear, as it must be.

### R1 — carryover key collision: CONFIRMED, fixed

`carryover` is `PRIMARY KEY (run_id, batch_id, reason, sku)` written with `INSERT OR REPLACE`,
and **two producers were both emitting `reason='unplaced'`**: `mgr.carryover_rows(i)` (the whole
standing put queue, a LEVEL) and the pick `_shortfall` (this batch's miss, a FLOW). Where both
were non-zero the pick row overwrote the put-away row and the quantity was **gone, with no
error**.

Reproduced at scale: **500 units destroyed by 3 colliding keys.** Fixed in `5776157` — the pick
shortfall is now `unpicked_unstocked`, and `_insert_carryover` **raises** on a duplicate key
rather than replacing.

### R2 — `cut` is a level labelled a FLOW: CONFIRMED, fixed

`recv_cut == recv_depth` in **200 of 200** batches, zero exceptions — they are the same
measurement at the same instant, because "how many were still standing when the whistle blew"
*is* the depth at that moment. Summing therefore counts a unit once per batch it waits.

`Diagnostics/receiving_report.py` published **`SUM = 619,418` against a dock that never exceeded
6,162 — 101x**, as its headline number. Fixed in `36f494f` (the reporter now publishes
`cut_batches`, 161 of 200, and `depth_max`) and `a49e4d2` (four surviving `FLOW` labels in the
source declarations).

The distinction the labels kept losing: `cut` **is** reset every batch, like the three real flows
beside it. Being reset per batch makes a counter *per-batch*; it does not make it *summable*.

### R3 — the empty-bin `continue`: REFUTED

Predicted to delete demand silently. Demand closes in 200 of 200 batches, so at this scale it
does not. The branch still has no direct test coverage — a coverage gap, not a live defect.

### R5 — a crew clock stalling permanently: REFUTED

The positive-feedback stall did not occur in 200 batches with all three carries starved. The
START gate is why: a worker already past the whistle begins nothing new, but the job in progress
finishes, so overtime is bounded by one job per worker and the clock always advances.

### R7 — the dock absent from `carryover_rows`: DECIDED, fixed

Never a measurement — a decision, and it was taken: **add the dock, keep the classes visible.**
`queue_contents` emitted a `'dock'` kind and `carryover_rows` did not, so the two surfaces
disagreed about what the unbinned backlog is; on this run that was 52,479 rows one of them
denied, and a consumer sizing the backlog from `carryover` missed every unit still on a trailer.

The two classes stay distinguishable because they are different problems:

| class | reasons | what it means |
|---|---|---|
| placement failure | `unplaced`, `held` | was offered a bin or floor space and did not get one |
| pre-placement | `dock` | never offered anything — still on a trailer |

Sum all three for the backlog; filter to the first two for placement. The `reason` column now
documents all three families and one instruction: **never `SUM(qty)` across the whole table**,
because the pick side's `unpicked_*` rows are FLOWS and these are LEVELS. (`b7b55ed`)

### R4, R6, R8 — not reached

R4 (`items_demanded`'s two definitions) is consistent at scale, but the two call sites were not
separately audited. R6 (batch-resume with `--cut-at-day-end` alone) needs a resume, and this was
a single uninterrupted arm. R8 (`lift_cache` never cleared) was not instrumented.

---

## 3. Found by the invariant workflow, not predicted

Five defects the plan did not anticipate. Each was invisible; each is fixed.

- **Every pick duration was `0.0`** — all 29,657 rows of the 200-batch run. Schema moved
  `31cb7d1b1199` → `ce01ca0095b2` to make `duration` nullable, and pick rows now write NULL,
  because a pick row is a *state change* and the work is the span between two of them. On the
  post-fix verification run, 668 of 12,857 rows carry a real duration. (`5cfdfbe`)
- **The `uids_contiguous` check was unsound** and was removed. It failed a provably healthy run
  (allocated 0..28, observed `{0..24, 26, 28}` because two put queues were idle) and would have
  *passed* the collision it existed to guard. Disjointness — the half that works — was kept.
  (`36f494f`)
- **`per_run` mixed two units of account in one row**: `*_depth` counts storage units,
  `in_transit` counts merchandise pieces, adjacent, unmarked, with a gap reaching 2.00x. The
  columns now carry `_units` / `_pieces`. (`de6a66e`)
- **Three DDL comments no longer described their column** — `work_events.role` omitted
  `'receive'` (2,412 such rows), `put_queue_state.kind` omitted `'dock'` (52,479). (`ebe0c46`)
- **`work_day` reads 0 in every batch of every run**, correctly — the continuous release schedule
  has no day structure — but that is *not* evidence the day machinery was off. On this run
  `work_day = 0` throughout while the whistle cut **1,418 times across 14 shifts**. The release
  day and the crew shift are two different days; both declarations now point at
  `work_events.shift_index`. (`a49e4d2`)

---

## 4. What this run does NOT prove

Stated plainly, because a green reconciliation invites over-reading:

- **The lead-time seam is dark.** `avg_lead_time_mean = 0.0` for the whole run. Nothing about
  in-transit timing was exercised.
- **The split ran, but what it proved is narrower than first written here.** The original
  wording — "all 2,412 packs routed to `store_pallet`, the cart and fulfillment queues stayed
  empty, the routing is proven" — overstated it, and is retracted. The only independent evidence
  was a uid gap showing the cart *crew* never worked, and that cannot tell "nothing was ever
  admitted to the cart queue" apart from "things were admitted and none could be placed" — the
  second being a growing `unplaced` backlog, which is a defect rather than a configuration.
  A separate audit did close the adjacent worry: `PutQueueSet.route` is first-match-wins on
  `unit_category` and *raises* `LookupError` rather than dropping a unit, and the split set
  contains no `ANY` catch-all that could swallow a singleton — so the predicate itself is sound.
  What remains unproven is the queue population. The settling query, on a fresh split run:
  `SELECT queue, SUM(admitted), SUM(placed), MAX(depth) FROM put_queue_state GROUP BY queue`.
  Contention between streams is untested either way.
- **Single arm, single channel.** No cross-channel interaction, no `_frozen/` tree, no resume.
- **Growth was fitted on the skus knob only**, at 300 and 2,400 SKUs — well short of the 76,500
  default catalogue.

---

## 5. The standing risk: two test tiers in no gate

`Tests/calltree/` and `Tests/bench/` are **hand-run**. Nothing in the routine suite collects
them, so a break there is reported by nobody — and two things were found rotting there during
this work: **three dead frozen-oracle tests** (`_WaveAsPool` had lost `prefers_low`, confirmed
pre-existing by stashing) and the **split put-away configuration, which had never executed
outside a unit test**, which is exactly why S1 survived.

`Tests/bench/coverage_e2e.py` additionally hands every worker a `queue.Queue()` it never drains
and prints DONE regardless of what happened, so a green run through it is not evidence.

Before trusting any number out of either directory, run `python -m pytest Tests/calltree -q`
first and read the result.
