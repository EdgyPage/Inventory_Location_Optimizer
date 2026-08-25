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
| **The run params cannot say what day length a run used**, so two runs are not comparable. | step 3's debt, closed in step 4 |
| **No end-to-end run with the cut on**, anywhere in the verification. | step 4 |
| **Every cut task reports its full planned workload against partial time**, so a truncated task reads as artificially efficient. No realized-items field exists to fix it with. | step 4 |
| **No bound on the carry, and no test that it is bounded.** A carry that grows every batch is a runaway that looks like demand. | step 5 |
| **The analysis layer's time axis is never widened**, so published throughput figures go wrong under any schedule with gaps in it. | step 8 |

## 5. The sequence that replaces it

Each step is independently verifiable, and no step leaves a producer without a consumer.

| # | Commit | Byte-identical? | Status |
|---|---|---|---|
| 1 | A skipped batch closes its own books: drains its put-away records, writes a zero-duration row, and `thr_batch` goes NaN rather than 0.0. | Yes, but **vacuously** — no arm in the sweep skips a batch, so the changed path never runs. Five behavioural sabotages are the evidence instead. | **done** `6d48907` |
| 2 | `WorkDay` + `ReleaseSchedule` as pure kernel values, fully tested, **not wired**. | Yes — nothing imports them, and a ratchet enforces it. | **done** `3c19dc0` |
| 3 | Wire the schedule to the release instant; record which day a batch is in and whether its slot was missed. | Only `batch_stats`' two new columns move; its pre-existing columns are 68/68 identical. | **done** `cf2446b` |
| 3b | The **event-by-event** lockstep test, written against UNCHANGED loops and passing on them. | n/a — a test only. | **next** |
| 4 | The day cut in both loops **and** the call site that passes `day_end`, in ONE commit. Plus: realized items on `TaskStats`, the day length in run params, and an end-to-end run with the cut on. | No, by design. | |
| 5 | `PendingDemand`, consuming the cut's carry — ONE definition — plus unpicked-demand rollover, a bound on the carry, and a test that the bound holds. | No, by design. | |
| 6 | Put-away and inbound rollover. | No. | |
| 7 | Resume: day clock, pending demand, held items, queue depths, the arrival stamp counter. | Resume-at-N equals a straight run. | |
| 8 | Analysis: widen the time axis so a schedule with gaps does not corrupt published throughput. | No — it corrects figures that are currently wrong under a paced schedule. | |

**Step 3b is a precondition, not a nicety, and it has to come first for a reason that is easy
to get backwards.** Every lockstep guard today compares an AGGREGATE, so two loops could stamp
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
