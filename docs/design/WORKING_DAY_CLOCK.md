# The working-day clock — what the first plan got wrong, and the sequence that replaces it

**Status:** in progress — steps 1 and 2 of §5 are done, the rest is not.

Written before any of it was built, because a read-only survey found that the approved
four-commit plan cannot be executed as written: two of its commits collide on an API that
neither ships, one commit's entire output is consumed by nobody, and no commit anywhere turns
the feature on.

Kept current as steps land. Where a step ships differently from the plan, the difference and
its reason are recorded rather than the table being quietly edited.

Everything below is either quoted from the code or was checked against it. Where a claim in the
original plan turned out to be false, the false version is kept alongside the correction —
deleting it would let the same wrong assumption be re-derived later.

## 1. What is being built

Picking, put-away and (later) inbound should share one clock that can be defined as a working
day. Batches are released within that day. Work that does not finish by the whistle rolls over
to the next shift, **mid-task**: a picker stopped part-way through a task has its remainder
flattened to `(item, quantity)` pairs and fed back through the existing task-creation machinery.
Separately, and independently of the clock: items a batch does not pick no longer vanish, they
roll into the next batch.

Four things follow, and only the first is a clock:

| | |
|---|---|
| **release** | when batch *i* starts, decided by a schedule rather than by batch *i−1*'s duration |
| **cut** | stopping a picker mid-task at the day boundary |
| **carry** | turning the stopped remainder, and unpicked demand generally, into next batch's input |
| **resume** | all of the above surviving a `--resume` |

## 2. Five claims in the original plan that are false

### 2a. The empty-batch clock behaviour is a tested contract, not a bug

The plan called it "the `arm_clock` deadlock … a documented deadlock", and proposed commit 16
fix it. Three things are wrong with that.

**It is not a deadlock.** Nothing hangs; the batch loop keeps iterating and the arm terminates
normally. An empty batch contributes zero elapsed time to the absolute axis. The right word is a
**clock stall**.

**The stated consequence is false.** The plan said "every later batch releases at the same
instant". A single empty batch does not pin all later batches — batch *i+1* releases at the
instant batch *i−1* ended, as if the empty one took no time. All later batches coincide only in
the degenerate case where every remaining batch is empty. The accurate statement: *the absolute
axis under-counts by exactly the wall time an empty batch should have consumed, and the
under-count is cumulative.*

**It is asserted by a test.** `Tests/unit/test_arm_clock.py`'s
`test_a_skipped_batch_cannot_advance_the_clock` compares source indices and asserts the skip
guard precedes the clock advance. The runner states it in prose too: *"A SKIPPED (empty) batch
never reaches here and so does not advance it — which is also why the epoch cannot be recovered
downstream by a cumsum over `batch_stats` rows."*

So the commit that changes this is **reversing a stated decision**, and must delete and replace
that test. It is not fixing an oversight, and its commit message should not claim to be.

### 2b. The put-away drain leaks on the same `continue`

Not in the original plan at all. The repo's only `drain_putaway_records()` call sits below the
skip guard, so a skipped batch never drains it — even though `check_reorders()` has already run
above the guard and produced put records for that batch. Those records are then stamped against
a later batch's epoch. Same root cause as the stall, and it belongs in the same commit.

### 2c. There *is* a lockstep test; what is missing is narrower

The plan carried a landmine saying `fast_pick.py` "admits there is no real lockstep test for
travel math". That comment was fixed at some point and now names its guards correctly. Three
lockstep tests exist:

| test | compares |
|---|---|
| `test_travel_decomposition.py::test_pick_fastpick_decomposition_lockstep` | the two loops' travel breakdown and axes |
| `test_scheduler.py::test_pick_fastpick_lockstep_under_lpt` | `max(event time)` |
| `test_picker_clock_carry.py` | done-time and event count, four start times, both schedulers |

**Every one of them compares an AGGREGATE.** Two loops could stamp identical totals on
differently-shaped event streams and pass all three. The gap worth closing is an
**event-by-event** comparison — type, time, aisle, sku, quantity and all five travel fields, per
event, in order. A day cut is exactly the change most likely to move a stream's shape while
leaving its totals alone.

### 2d. The stated verification for the cut is arithmetically impossible

The plan asks for `pick_travel + non_pick_travel + cart_move == duration` on every cut task.
That does not hold today and cannot: **handling time is a fourth clock advance with no
decomposition field**, deliberately. The repo already states the true identity, in
`test_travel_decomposition.py`:

    pick + nonpick + cart + handling + other == duration

worked in that test's own fixture as `53.125 + 10.940 = 64.065 s`. The cut's verification must
be restated as either that identity against `cut_time − task_start_time`, or the
accumulator-conservation form: the five travel fields summed over the cut task's events equal
the task's travel and cart clock advances — i.e. no second is dropped by the cut.

### 2e. The preflight canary fires less often than assumed

`strategy_runner.py` is in `SHAPE_SOURCES`, so a commit touching it fires the canary pair — true.
But **neither picker loop is**. A commit confined to `Pick.py` and `fast_pick.py` and their tests
does not fire it. Worth knowing, because it changes what a picker-loop commit has to run.

## 3. Six defects in the commit sequence itself

These are worse than the factual corrections: the four commits do not compose.

### 3a. Nothing ever turns the feature on

The single production construction site for the pick simulation is never touched by any of the
four commits. `day_end` is therefore never passed, the `WorkDay` methods that compute it have no
production caller, and the schedule object yields only a START instant — it never produces a day
END at all. **The user's headline requirement ships unreachable.** A wiring commit is missing
from the plan entirely.

### 3b. The carry is defined twice, incompatibly

The cut commit builds a carry: a shared helper, a `carried` property on both simulations, an
out-parameter, a widened worker return arity, and a per-picker merge. The rollover commit then
derives the carry independently as the residual `effective − picked`, and **never reads
`carried`**. So either the cut's entire surface is dead code, or a later commit wires both and
double-counts exactly the day-cut units. There must be **one** definition of the carry, and the
producer and consumer must land together.

### 3c. Release and cut cannot both be on

With a schedule releasing batch *i+1* at a fixed instant and a cut stopping batch *i*'s pickers
at the day boundary, the same picker can be working two batches simultaneously. Whichever of
those two is built second has to state what happens to a picker whose previous batch overran its
release — and neither commit does.

### 3d. Two commits collide on a name with incompatible types

The rollover commit introduces a `pending` object with methods; the resume commit rebinds the
same name to a plain dict from a checkpoint, then calls the object's methods on it. Neither
design provides serialisation for the object. This is a hard `AttributeError` on the first batch
of any resumed arm.

### 3e. The resume commit calls a method the clock commit does not define

It clamps against `schedule.release(i)`; the clock commit ships `release_at(index, ready_at)`,
which on the shipped default *returns its argument* — so there is no schedule-derived floor to
clamp against at all. The two also disagree on what the schedule object is called.

### 3f. `unpicked_daycut` is not zero before the cut lands

The rollover commit argues the label is safe because the bucket must be empty until the cut
exists. It is not. The residual already absorbs two live-stock clamps — both picker loops clamp
the planned quantity to what is actually in the bin, and one of them re-clamps in a second phase
that the runner already documents as a drift source. Those land in the same bucket. The label
would be wrong the day it shipped.

## 4. What the plan does not cover at all

Each of these is now folded into the §5 step that CREATES the problem it describes, rather
than left as a trailing wish-list. The rule that made the sequence work — no producer without
a consumer — extends to this: no defect without its fix in the same step.

| Gap | Where it lands |
|---|---|
| **Put-away and inbound never roll over.** The requirement says "if the work from pick or puts or inbound does not complete"; only picking was addressed. | step 6 |
| **Nothing records which working day a batch belongs to**, so no analysis can group by day. | step 3 — **done**, `batch_stats.work_day` |
| **The run params cannot say what day length a run used**, so two runs are not comparable. | step 4c |
| **No end-to-end run with the cut on**, anywhere in the verification. | step 4b — **done** |
| **Every cut task reports its full planned workload against partial time.** | step 4a — **done** |
| **No bound on the carry, and no test that it is bounded.** | step 4b — **done**, and the answer is that it is NOT bounded, deliberately. At a 1,000 s day the backlog never clears. That is a warehouse which cannot keep up, and the carry exists to make it visible rather than to cap it; a test asserts the backlog stays reported. |
| **The analysis layer's time axis is never widened**, so published throughput figures go wrong under any schedule with gaps in it. | step 8 |

## 5. The sequence that replaces it

Each step is independently verifiable, and no step leaves a producer without a consumer.

| # | Commit | Byte-identical? | Status |
|---|---|---|---|
| 1 | A skipped batch closes its own books: drains its put-away records, writes a zero-duration row, and `thr_batch` goes NaN rather than 0.0. | Yes, but **vacuously** — no arm in the sweep skips a batch, so the changed path never runs. Five behavioural sabotages are the evidence instead. | **done** `6d48907` |
| 2 | `WorkDay` + `ReleaseSchedule` as pure kernel values, fully tested, **not wired**. | Yes — nothing imports them, and a ratchet enforces it. | **done** `3c19dc0` |
| 3 | Wire the schedule to the release instant; record which day a batch is in and whether its slot was missed. | Only `batch_stats`' two new columns move; its pre-existing columns are 68/68 identical. | **done** `cf2446b` |
| 3b | The **event-by-event** lockstep test, written against UNCHANGED loops and passing on them. | n/a — a test only. Passes today across both schedulers, both lane models, 1 and 3 pickers, four start-time epochs, five fixtures and twelve randomized workloads. | **done** `c68770e` |
| 4a | Realized items and bins on `TaskStats`, so a truncated task cannot report a full workload against partial time. | Only `task_stats`' two new columns move. | **done** `de1ee6e` |
| 4b | The day cut in both loops, the call site that passes `day_end`, and the carry re-picked next batch — one commit, so no half is dead. Includes the end-to-end run. | Cut OFF: byte-identical. Cut ON: different by design. | **done** `4e6b3c6` |
| 4c | The day length and release cadence in the run params, so two runs with different days are distinguishable. | Adds run metadata only. | **done** `81884b8` |
| 5 | Rollover for the OTHER cause — the live-stock clamp, `unpicked_unavailable` — merged with the cut's carry under one reason column. | Recorded always, rolled over only on request; the recording half is byte-identical. | **done** `6783e4a` |
| 6 | Put-away rollover: the whistle is a START gate on the put crews, and `put_queue_state.cut` says what it left standing. | Cut OFF: byte-identical (1027/1164 digests; the rest are the two new columns and the two timestamp tables). | **done** `90cd7a8` |
| 7 | Resume: batch granularity REFUSES while the carry is on, because `_pending` is in no checkpoint and resuming would drop demand. Strategy granularity — the default — replays from batch 0 and needed nothing. | Default path untouched. | **done** `d410ac0` |
| 8 | Analysis: `throughput_elapsed`, measured against the elapsed day rather than the batch makespan. | Identical to `throughput` under the continuous default, bit for bit. | **done** |
| 6b | Inbound HOURS — does the receiving dock have a day of its own? | n/a — needs a decision first. | **blocked on §8's question** |

**Step 7 shrank to one guard, and the reason is worth keeping.** The step listed five pieces
of state to carry across a resume (day clock, pending demand, held items, queue depths, the
arrival stamp counter). Under the default granularity none of them needs carrying: a partial
arm's DB is deleted and the arm replays from batch 0, so every one of them is rebuilt from
nothing. Batch granularity is where the day clock genuinely broke it, and not in the way its
existing warning describes — "not bit-identical" reads as a rounding difference, while
`_pending` living only in the worker's locals means a resume *drops demand* and the run then
reports throughput it did not earn. That is a conservation break, so it raises.

**Step 8 is a second metric, not a corrected one.** `thr_batch` divides by the batch
makespan and answers *how fast did the crew work*; `thr_elapsed` divides by the gap between
consecutive releases and answers *how much did the day produce*. Under the continuous default
these are the same number to the last bit — the runner sets `arm_clock = batch_start_time +
duration` and `release_at` returns it unchanged — so nothing published moves. They separate
only under a paced schedule with slack, and a scheduling change moves them in *opposite*
directions: fewer, fuller waves raise what a day produces while leaving the working rate
alone. Neither can stand in for the other, which is why this is an addition. It is a declared
`Quantity` (appended at the END of the table, so every significance-CSV row keeps its index)
rather than an ad-hoc frame column, so the era gate and the figure registry both see it.

**Step 6 split, because half of what it named was already built.** "Put-away and inbound
rollover" was one row on the assumption that inbound needed the same machinery. It does not:
an arrival a full floor cannot take is already `_held` and already retried by the next
drain's `_admit_held`, with `blocked` counting the refusals — that *is* rollover, built with
the staging limit in step 13. What was genuinely missing on the inbound side was a *name* for
the arrival, so that a shipment split across trailers packs per delivery rather than as one
lump; that landed separately as `Warehouse/operations/inbound.py` (`c380a31`). Step 6b is now
only the question the day clock actually raises: whether the receiving dock has hours of its
own, distinct from the put crews'. **It needs a decision before it needs code — see §8, and
it is the only step of this sequence still open.**

**The put cut is a START gate, and that asymmetry with the pick cut is deliberate.** A pick
path is long and divisible, so it truncates mid-bin. A put is one unit into one bin. A
completion gate ("refuse anything that would end late") needs the duration, which is known
only after the bin is chosen — so it would mean choosing a placement and then un-choosing it,
past `_execute_placement`, which is the single bin-mutation commit point precisely so that no
path does that. Overtime is bounded by one put per worker, which is what the physical thing
does.

**Step 3b is a precondition, not a nicety, and it has to come first for a reason that is easy
to get backwards.** It is now done, and it earned its place: pooling one loop's
`pick_travel_x` onto a single event reshapes 18 of 62 events with every sum bit-identical,
and the travel breakdown, the axes, the makespan and the event count all still agree. Only
the event-by-event comparison sees it. That case is itself a test.
 Every lockstep guard today compares an AGGREGATE, so two loops could stamp
identical totals on differently-shaped event streams and pass all three — and a day cut is
exactly the change most likely to reshape a stream while leaving its totals intact. Writing
the event-by-event comparison AFTER the cut would leave no way to tell whether a divergence it
reports is the cut or a difference that was always there. It has to pass on unchanged loops
first; that run is the baseline.

**Step 1 shipped narrower than this table first said, and the reason is worth keeping.** It was
planned as "clock stall + drain leak, reverses a stated decision". It did not touch the clock:
there is no principled duration for an empty batch until a schedule exists to say what a
day-slot costs, so advancing it in step 1 would have meant inventing a number. The contract
test still asserts the skip precedes the advance, and step 3 is where that is reversed. What
step 1 could fix without inventing anything — the leaked put records, the missing row, and a
zero-duration batch reporting zero throughput instead of NaN — it fixed.

**Flaw 3c above (release and cut cannot both be on) resolves itself, and not by choice.** The
model has no picker contention: a batch cannot begin while the crew is still working the
previous one. So `release_at` clamps to the ready instant, the schedule is simply *missed*, and
`missed_by` records by how much. The cut makes overruns less likely, not more, so the two
compose. Step 3 states this where the wiring happens.

**Step 3 left one debt, named here so it is not lost.** A run can now be given a non-default
day length and release cadence, and nothing writes either into the run params — so two runs
with different days are indistinguishable after the fact. It is small, it belongs with the
first commit that makes a non-default schedule worth running, and that is step 4.

### This supersedes the approved plan

The four-commit plan this document corrects should not be worked from again; §2 and §3 say why.
This §5 is the operative sequence, and it is kept current as steps land rather than being
re-derived somewhere else. A second plan document would give one thing two homes, and the
one that is not being edited is the one someone will read.

Two naming constraints carry through all of it:

- **Do not call it a shift.** `timeline.shift_index` contracts that shifts *label* the timeline
  and never schedule against it, and that contract is restated in five other places — including
  a persisted DDL comment and a user-facing capability caveat. The dispatch-affecting concept is
  a `WorkDay`.
- **Do not follow `timeline.py`'s own suggested seam.** Its docstring points at the put-away
  budget parameter as the place a dispatch boundary belongs. That parameter defers a whole wave
  and never truncates one, which is the wrong granularity for a mid-task cut — and it has no
  production caller, so adopting it would mean building on an untested path as well.

## 6. Stale things to fix while nearby

- `Warehouse/kernel/timeline.py`'s module docstring claims picker clocks are "reborn at 0.0 at
  the start of every batch". The `start_times` carry made that false — both loops now start at a
  passed-in `t0`. It also claims the absolute axis is computed in the analysis layer by a bare
  cumulative sum; the runner computes it directly.
- `Warehouse/kernel/README.md`'s module table omits two modules that have been in the kernel for
  some time. Nothing verifies that table, so it will keep drifting.

## 7. What the cut actually costs, measured

A 300-SKU arm over ten batches, per-batch conservation asserted on every batch of every run.

| day length | picked | cuts | backlog |
|---|---|---|---|
| none / 100,000 s / 20,000 s | 4,000 | 0 | never carries |
| 5,000 s | 3,983 | 20 | spikes to 345, clears |
| 2,000 s | 3,954 | 43 | oscillates, clears |
| 1,000 s | 3,477 | 88 | never clears — **growing** |

The last row is the honest answer to "is the carry bounded": it is not, and it should not be.
A day too short for the demand leaves work behind every batch, and the carry's job is to make
that visible rather than to cap it.

One accounting trap, recorded because the first measurement produced a scary and meaningless
"LOST 345": summing SCHEDULED work across batches is not a conservation quantity. A batch's
scheduled work includes the previous batch's carry, so the sum double-counts every carried
unit. The invariant is per batch — `picked_i + carried_i == scheduled_i` — and it holds.

## 8. The open question step 6b needs answered

Put-away and picking now share one whistle, because they share one crew's day. Inbound does
not obviously share it, and the model currently has no opinion:

- **A lead time is a CALENDAR quantity.** `check_reorders` advances it in steps 0–3, above the
  deadline, deliberately — a trailer in transit does not stop moving because the warehouse
  went home, and an order that arrives at four o'clock has arrived.
- **But receiving is LABOUR.** Someone unloads the trailer, and that someone has a day. Today
  the model has no unload step at all: `_release_to_stock` packs the arrival and `_admit` puts
  it on a queue, both free.

So the honest state is that inbound *rollover* exists (a full floor holds the item, the next
drain retries it, `blocked` counts the refusal) while inbound *hours* do not. Adding them
means deciding one thing first: **is the receiving dock a fourth crew with its own
`PutQueueSpec`-shaped hours, or is unloading part of the put crews' day?**

The first is more faithful and costs a fourth actor space plus a fourth clock in every
snapshot. The second is free and says that a warehouse which cannot put away also cannot
receive — which is what a shared-crew site looks like, and false for a site with a dedicated
receiving team. `Warehouse/operations/inbound.py` deliberately holds no clock so that either
answer can be built on it, and `LoadPlan` already carries what an unload step would need to
cost itself (`unit_count`, `packed_qty`, `tier_mix`).

Not a coin-flip: it changes what a short day *means*, and every published throughput figure
under a paced schedule depends on it. It is the user's call.
