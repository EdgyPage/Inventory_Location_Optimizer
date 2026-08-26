# Stress-test findings — put-away queues, the working-day clock, the receiving dock

The machinery shipped in `dd68e00..fa18639` was correct at 4–8 batches and had never run at
scale. This is what running it at scale found: two halves, **growth** (does anything get
super-linearly worse) and **correctness** (does the carry close).

Reproduce with:

```bash
python Tests/calltree/calltree_growth.py --knob skus --config split_staging4   # the ladder
python Tests/calltree/calltree_growth.py --knob skus --config none             # the control
python Diagnostics/receiving_report.py <run>                                   # the carry
```

`--config` is load-bearing and did not exist when this document was first written. Without it
no ladder set `put_timing`, `put_split`, `put_staging` or `recv_crew`, so `_admit_held`, `_held`
and `HeldItems` were **structurally dead in every runnable rung** — the line here used to say
"reproduce with `calltree_growth.py`" and that was false: the tool could not express the
configuration these numbers were measured in, and the figures below came from a throwaway
probe. They are now archived artifacts under `Tests/calltree/out/archive/`, tagged `cfg-`.

Neither tool is in a gate — see section 5.

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

**96x fewer `route()` calls at 2,400 SKUs** — the ratio itself grows with the catalogue, so at
300 SKUs it is 15.9x; wall 41.7 s → 21.7 s. Fixed in `68bf962`, pinned by
`Tests/unit/test_admit_held_early_exit.py` — which asserts the call **count** (exact and
deterministic, where a stopwatch would be flaky) *and* separately that the early exit matches an
exhaustive walk item-for-item and in order, because an exit that dropped or reordered an item
would be worse than the slowness it cured.

**Why it survived every prior test:** with `staging=None` the held list is always empty and the
refill loop runs exactly one pass. The defect was unreachable until the split put-queue
configuration became selectable with real staging limits (`53bee99`).

### The cost is staging, not the split

A 2x2 decomposition, because "split queues are slow" and "backpressure is slow" are different
findings with different remedies:

| configuration | exponent |
|---|---|
| baseline | k = 1.100 |
| + split queues, no staging | k = 1.013 |
| + staging, single queue | k = **1.450** |

The split costs nothing. **Staging carried all of it.** These are *wall* fits, and the exact
instrument was available in the same artifact — see the correction below for why that matters.

### CORRECTION — `68bf962` did NOT close the growth term (the partition did)

This section previously read: *"baseline k = 1.238 against staging k = 1.281 — the exponents
match, and what remains is a 2.6x constant factor, not a growth term … This is now the former."*
**That was wrong, and it is retracted.** An adversarial audit of this document caught it and the
finding was then reproduced directly.

What `68bf962` actually did: it removed the *work per touch*, not the touches. The early exit
breaks out of the `route()`/`admit()` loop, but the original then did `still.extend(rest)` into a
fresh deque — so every call still copied the entire held list. O(H) per call, O(H) calls.

Measured at 20 batches, `staging=4`, split queues, over four rungs:

| SKUs | 300 | 600 | 1,200 | 2,400 | OLS k |
|---|---|---|---|---|---|
| held-item touches | 308,792 | 998,218 | 3,743,071 | 13,041,581 | **1.81** |

against `k = 1.82` before the exit existed. The exponent did not move.

**The root cause is deeper than the copy**, and this part is new. The exit fires on
`len(blocked) >= len(self.put_queues)` — *every queue in the set*. On a store-only catalogue the
three-queue split routes to `store_cart` and `store_pallet` only; `fulfillment` never receives
anything, so `blocked` tops out at 2 of 3 and **the exit is unreachable**. Measured directly: 3
queues in the set, 2 ever routed to, 2,877 calls, 998,218 touches, ~347 items examined per call.
The exit is reachable only in the single-queue configuration — which is the one configuration
where staging is `None` and the held list is always empty.

**Blast radius today is zero** and stays that way until someone turns the split on:
`PUT_QUEUE_SPLIT = False` and all three `PUT_*_STAGING = None` in
`Optimization/config/settings.py`, and `put_queues_spec()` returns `None` without the split. No
archived run has executed this path. But the 200-batch stress run used `staging=8` with the
split, so it is the configuration on deck.

**CLOSED, on the third attempt.** `_held` is now PARTITIONED per queue (`HeldItems`), so a
full queue is skipped in O(1) and there is no exit condition left to get wrong. Routing happens
once, when the item is held, so the retry does none. Same ladder, same parameters:

Re-measured 2026-08-26 with the committed instrument (`--config split_staging4 --knob skus`),
which reproduced the throwaway probe's counts exactly:

| SKUs | 300 | 600 | 1,200 | 2,400 | OLS k | r² |
|---|---|---|---|---|---|---|
| `route_calls`, before | 308,792 | 998,218 | 3,743,071 | 13,041,581 | 1.84 | 1.00 |
| `route_calls`, after | 12,394 | 22,968 | 45,164 | 85,272 | **0.93** | 1.00 |
| reduction | 25x | 43x | 83x | **153x** | | |
| `held_retry_touches`, after | 7,659 | 14,262 | 28,138 | 53,178 | **0.94** | 1.00 |
| `refill_passes`, after | 1,557 | 2,876 | 5,653 | 10,615 | 0.93 | 1.00 |

The reduction *itself* grows with the catalogue, which is what distinguishes removing a growth
term from removing a constant. Every flow is under the framework's `FLAG_COUNT_EXP = 1.30`, and
**no put-away symbol appears in the offender list at any configuration.**

The 2x2 also re-ran, and this time on call counts rather than walls:

| config | wall @2,400 | held path | verdict |
|---|---|---|---|
| `baseline_put` | 2.61 s | never executes | — |
| `split` | 2.59 s | never executes | **the split costs nothing** |
| `staging4` | 5.87 s | 42,560 appends, k=0.94 | the floor is the whole cost |
| `split_staging4` | 5.98 s | 42,535 appends, k=0.94 | the split adds nothing on top |

`baseline_put` and `split` produce byte-identical flows. It is the **floor**, not the split,
that creates a backlog at all — which is why `staging=None` kept the defect unreachable.

Two intermediate attempts, recorded so they are not retried:

- **An early exit alone** (`68bf962`). It cut `route()` calls 96x at 2,400 SKUs, which is real,
  but it still copied the whole list into a fresh deque per call — and the exit was unreachable
  anyway, per above. The in-place rewrite that removed the copy did not move the exponent
  either, because the walk and not the copy is what dominates once the exit cannot fire.
- **A parallel per-queue census** beside the single deque. Rejected: derived state that can
  drift from the deque, and a stale one makes the retry stop instantly and livelock, which is
  worse than the slowness it cures. The tests caught it in seconds. The partition needs nothing
  kept in sync, because the partition *is* the state.

The equivalence guard is `Tests/unit/test_admit_held_is_linear.py` (renamed from
`..._early_exit.py` — there is no early exit any more). It compares the partition against a
reimplementation of the ORIGINAL global age-ordered walk, a different algorithm rather than a
paraphrase of the one under test, and it pins the idle-queue case directly.

### NEW, from the re-measurement: staged placement re-scores super-linearly

Found only because the instrument can now run a staged configuration, and **not** a
consequence of the `_admit_held` work — the retry itself is provably linear above.

The staged wall does not grow like a constant multiple of the baseline. The ratio climbs
1.42 → 1.58 → 1.92 → 2.25 across the four rungs, and `t_reord` fits **k = 1.13 under
`baseline_put` against k = 1.475 under `staging4`**. The excess localizes cleanly: five
offenders appear under staging that are absent from the unstaged cell, and every one of them is
in the placement-scoring or bin-geometry path, not the held list.

| appears only under staging | k |
|---|---|
| `_TravelBalancedPool.__init__.<locals>.<lambda>` | 1.75 |
| `cost_model:height_multiplier` | 1.68 |
| `Aisle_Storage:Aisle.Bin.y_phys` | 1.67 |
| `Aisle_Storage:Aisle.Bin.x_phys` | 1.64 |
| `Aisle_Storage:Aisle.Bin.location` | 1.45 |

The plausible mechanism, **not yet verified**: a floor defers placement, so units land later
and in larger groups against a fuller warehouse, and each placement then scores more bins.
Whether that is inherent to deferral or a defect in the scoring path is a separate
investigation. Recorded here because it is the first thing this instrument found that nobody
was looking for.

Five further offenders (`_aisle_best` k=1.53, `_score_of` k=1.53, `delta_lift_idxs` k=1.52 and
its genexpr, `sum_lift`'s listcomp k=1.32) appear under **both** cells and on the default
`cfg=none` ladder too. Those are pre-existing, already recorded, and orthogonal.

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

### Memory: nothing to report, and that is now a measurement

`calltree_memory.py --ladder skus --config split_staging4`: every section's `k_mem` is
sub-linear (0.50–0.99, nothing flagged), and `put_queue.py` does not appear in the top twelve
allocation sites. The per-queue partition's dict-of-deques costs nothing measurable. Before the
`--config` pass-through the memory tool forwarded four arguments and could not express this
configuration at all, so "no memory cost" was an assumption rather than a result.

### Production scale: the deep tier, 10k to 80k SKUs

`--ladder deep --workers 18` — four real `run_simulation` sweeps (the full 34-arm suite each,
`--spec single` notwithstanding), 58 minutes total. This is the only measurement that reaches
past the 76,500-SKU default catalogue.

**`t_reord` fits k = 0.95, r² = 1.00** over 10k → 80k, walls 0.30 / 0.56 / 1.10 / 2.16 s. That
is the section containing put-away, at real scale, in real subprocesses. No offender flagged at
any rung. Together with the meso ladder's k = 0.94 on the retry flows, the `_admit_held` term is
closed at both scales.

Caveat worth stating: the deep tier runs `settings.py` defaults, so this is the **default**
path. `--config` is refused there by design, so the staged configuration's k = 0.94 is still
extrapolated past 2,400 SKUs rather than measured there.

### NEW, and not put-away: a knee in the simulation phase between 40k and 80k

Decomposing each rung's log into phases:

| rung | build+precompute | simulation | analysis |
|---|---|---|---|
| 10k | 1.1 m | 3.2 m | 1.3 m |
| 20k | 1.3 m | 4.4 m | 1.4 m |
| 40k | 1.4 m | 7.5 m | 2.3 m |
| 80k | 2.1 m | **27.4 m** | 3.9 m |

Per doubling, the simulation phase fits k = +0.45, +0.77, then **+1.87**. Analysis stays flat at
~+0.77 throughout, so it is not the analysis suite. The traced sections account for roughly 12
seconds of that 27-minute phase, so whatever grows is **outside every instrumented section** —
and `t_reord` is linear across all four rungs, so it is not put-away.

Not diagnosed. A knee at the top rung of a four-rung ladder on one machine has at least three
candidate explanations — a genuine threshold in some structure, memory pressure at 230,750 bins
× 34 arms × 18 workers, or cache behaviour — and telling them apart needs a repeat run and a
middle rung, not a guess. Recorded because it is real, reproducible from the archived artifact,
and nobody was looking for it.

For context on run cost rather than growth: the same 80k rung took 17.3 minutes on the
2026-08-20 archived deep ladder against 33.6 now. That comparison spans roughly 150 commits
including the entire receiving and working-day feature set, which added tables and per-batch
work, so it is **not** attributable to any single change here.

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
- **The STAGED growth was fitted on the skus knob only**, over 300/600/1,200/2,400 SKUs — well
  short of the 76,500 default catalogue. The deep tier reaches 80,000 and shows `t_reord` linear
  there, but only in the DEFAULT configuration; `--config` cannot reach the deep tier, so the
  staged path above 2,400 SKUs remains extrapolation. (The exponents ARE four-rung OLS fits, not two-point slopes: the
  framework returns `(nan, 0.0)` below three points and `fit_report` short-circuits, so a
  two-point exponent cannot be emitted. Earlier revisions of this document printed only the
  first and last rung, which invited the opposite reading.)

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
