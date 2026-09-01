# Run the pilot gate

Type: task
Status: resolved
Blocked by: 08, 18, 19, 21

## Question

Fix the pilot configuration, run the throwaway inbound-on `fifo` run, and **decide whether
the campaign runs at all**. This is the gate 08 declared: a config showing neither yard
contention nor binding cuts is a DECLARED STOP, not a knob to keep turning.

It graduates from the map's fog now (and not earlier) because 18 paid the CLI/run-spec seams
the whole inbound family had deferred, 19 made the objective reportable, and 21 unblocked
the mandatory `fifo` rider — so nothing stands between the map and a run.

### What the run must be

ONE deliberately small, THROWAWAY run — **not** a reusable phase-2 cell (the arm set is not
known until phase 1 ends, so reuse would be circular). Inbound ON, both registry knobs at
`fifo`, standing yard on, zoning off, scheduler `lpt`, rollover OFF, on a provisional
two-or-three-rule arm set.

### The three questions it answers at once

1. **Does the config produce 10's criterion (a), YARD CONTENTION** — standing trailers
   regularly exceeding free doors at drain start? Read from the `yard.binding` figure's
   absolute panel and its contention sentence (`_contention_note`), which prints
   `N of M drains had trailers standing with no free door`. Not a ranking — an absolute
   value under `fifo`.
2. **Does it produce criterion (b), BINDING CUTS** — drains regularly ending with an
   unreached trailer or units still on a staged one? `binding_cuts` is a declared quantity;
   read it as a COUNT OF DRAINS, never a sum of the levels (`cut is a level, not a flow`).
3. **Where does `INBOUND_FEE_THRESHOLD_DAYS` sit** for nonzero, NON-SATURATED overage? This
   is an analysis RE-REPORT over the same run, not a sweep — 07 stored stamps raw precisely
   so the fee axis is re-reportable under a new threshold without re-simulating.

Plus the bench 08 asked for: the `'all'` futuresight window's cost, whose real multiplier is
**drains per batch** (the n² batch-copies are the minor term). The bench decides whether the
`demand_v` memo — 06's "legal-keyed-not-built" case — is needed before phase 2, and it needs
drains-per-batch from this run whether or not a futuresight arm is run here.

### The exit, named now so it cannot become an open-ended search

**On failure: BOUNDED retuning** — at most a couple of attempts moving σ and the door count
(and note that the pilot may demand more SKUs rather than more batches: the batches knob
saturates every backlog level, so put-away and receiving pressure show on the SKU knob) —
then a **DECLARED STOP**. If contention will not bind, that is not a knob to keep turning;
it is the finding that this model carries no structural inbound pressure at the series'
scale, it invalidates the campaign's premise, and it is reported as such.

### What the answer must record

The pilot's outputs are phase 2's inputs, and they land in ONE place —
`Optimization/config/whatif_config.py`'s `PHASE2_*` constants, which are pre-pilot
placeholders by construction (the H points are derived from the threshold rather than typed
beside it). The answer records: the chosen arrival regime (lead median, spread, doors), the
calibrated threshold, the finite `w`, the drains-per-batch bench and its memo verdict, and
the go / retune / stop decision itself.

### Constraints

- **Nothing published.** Like phase 1, no pilot number is ever published; it is a throwaway.
- **The pilot leads are free to fix, but no seed knob exists.** `lead_seed` is `seed_world()`
  by decision, so the lead schedule is a world fact and every arm sees the identical one.
- **The domain TAG is `0x1EAD`** and is un-re-derivable — do not touch it.
- Read `yard.binding` as a figure, not a query: 17 made the gate a chart to look at.

## Answer

**GO — with three structural conditions the campaign spec must absorb before phase 2 runs.**
Both of 10's acceptance criteria hold, in a stable non-saturated regime, on a config this pilot
found. But finding it exposed that the receiving crew's capacity was never calibrated against
the picking clock, and that a single global receiving knob cannot serve two channels whose
loads differ 7.4x. The gate does not stop the campaign; it moves one decision in front of it.

### The config that passes

    --inbound-trailer-type 53 --inbound-standing-yard --inbound-dock-doors 4
    --inbound-lead-minutes 480 --inbound-lead-spread 0.7
    --recv-crew-size 4 --recv-day-seconds 43200
    --spec inbound_pilot --n-batches 75          # published depth (experiment-8)

Committed as the `inbound_pilot` spec (`whatif_config.SPECS`) so the gate is re-runnable: one
cell, zoning off, scheduler pinned to `lpt` (phase 2's — the scheduler moves batch DURATION,
and duration is what decides how many leads elapse before the next drain observes them), two
rules — `fifo` (phase 2's mandatory rider and reference) and `tmin` (a ranked wave) — both in
`Inbound.gain.FAITHFUL_GAIN_FAMILIES`. The inbound knobs ride the command line rather than an
inbound axis because the gate is ONE arrival regime, not a sweep, and because the two knobs
that actually decide whether the yard binds — the receiving crew and its day — have no cell
axis at all.

### 1. A receiving whistle is a PRECONDITION, not a tuning preference

`crew_clock.can_start(clocks, None)` returns True unconditionally, and `_unload_split` refills
every freed door from the whole frozen yard order. So with `--recv-day-seconds` unset (the
default — `RECV_DAY_SECONDS = None`) the crew has an unbounded day, the yard drains COMPLETELY
every drain, and `yard_end` / `staged_remainder_end` are structurally 0 forever while
`free_doors_start` is always `doors`. NEITHER criterion can fire. Not a knob to tune toward:
without it the gate is unmeasurable by construction.

### 2. Both criteria pass — and the two readings of criterion (a) disagree

`yard.binding` renders and prints its contention sentence on the figure, exactly as 17
intended: the gate is a chart, not a query. But 10 words criterion (a) as "standing trailers
regularly EXCEED FREE DOORS at drain start", while `_contention_note` implements
`yard_start > 0 AND free_doors_start <= 0` — ALL doors occupied. Different tests, and on a
slack config they give opposite verdicts (20k-SKU probe: loose true in essentially every drain,
strict 0-1 of 20). The STRICT reading is right and the implementation is right to be stricter:
the loose one is satisfied whenever the yard is merely DEEP, and a deep yard that still drains
completely within the drain changes only the ORDER trailers are worked in, never the SET —
which is exactly the case 21 showed `gain_myopic` gains exactly zero on. The lever needs the
clocks to cut. Recorded because the looser wording, read literally, would have passed the very
first probe and skipped the whole calibration.

On the passing config (`opt_fifo`, 75 drains per leaf):

| leaf | yard depth mean/max | free doors mean | contention (strict) | binding cuts | detention p50 / max | standing at end |
|---|---|---|---|---|---|---|
| lt0 / fulfillment | 44.4 / 93 | 1.53 | 46/75 | 48/75 | 1.61 d / 4.76 d | 70 |
| ltrand / fulfillment | 66.4 / 130 | 0.80 | 60/75 | 61/75 | 2.52 d / 5.74 d | 77 |
| lt0 / store | 191.5 / 644 | 3.25 | 14/75 | 14/75 | 6.86 d / 16.86 d | 0 |
| ltrand / store | 267.9 / 656 | 3.11 | 16/75 | 17/75 | 7.48 d / 22.51 d | 0 |

Non-saturated in the sense that matters: every store trailer emptied before the run ended, and
fulfillment's censored tail is 70-77 trailers against ~2,000 served — a tail, not a runaway.

### 3. What the FIRST attempt found, and why it is the campaign's real problem

The first pilot ran a physically plausible dock — 2 receivers, an 8-hour day — and produced a
catastrophe that reads as SUCCESS if only the two criteria are checked: contention 70/75,
binding cuts 71/75, yard depth to 2,208 trailers, detention p50 12.9-26.2 DAYS (max 75.9),
1,955 trailers still standing at run end, warehouse fill collapsing 85% -> 35%. **Missed share
49.6% / 48.8% on fulfillment.** Half the demand unserved is not a policy comparison, it is a
starving warehouse, and 08's decision rule ("missed share not degraded") cannot be read against
a baseline like that. `absolute_yard_depth` shows it exactly: stable at 200-400 through batch
~45, then monotone divergence to 2,200 — a queue crossing capacity mid-run.

The cause is a CLOCK MISMATCH BETWEEN DEPARTMENTS, structural rather than a bad knob value:

    _recv_deadline = _recv_day.end_of(_recv_day.index_of(arm_clock)) - max(arm_clock, recv_clock)

The receiving crew is granted **one day-REMAINDER per BATCH**. Measured batch durations at
published scale: **store 546,315 s = 19.0 working days**, fulfillment 74,194 s = 2.58. So the
dock is staffed one day for every 19 days of store operation. Worse, `cut_at_day_end` is off,
so `arm_clock` lands at an arbitrary point inside a day and the remainder averages HALF a day:
effective capacity is **crew x recv_day_seconds / 2 per batch** — measured 27,226 s against a
2 x 28,800 nominal (47%), and 38-53% of nominal across all four leaves.

So `--recv-day-seconds` does not mean what its name says once a batch outlives a day. It is a
per-batch LABOUR BUDGET and must be sized against the batch, not against a shift. Raising
capacity 3.2x (crew 4, 12-hour day) moved missed share 49.6% -> 22.0% (lt0 ful),
48.8% -> 15.9% (ltrand ful), 18.4% -> 3.5% (ltrand store) while KEEPING both criteria — which
is what makes this GO rather than a declared stop.

### 4. The channel asymmetry — one global knob, two warehouses

Receiving demand per batch at full catalogue, measured with a crew large enough that nothing
was cut (crew 8, 12 batches): lt0 store 10,195 s, ltrand store 22,337 s, lt0 ful 46,260 s,
ltrand ful 75,656 s — a **7.4x spread** against a single `CONFIG['global']` `recv_crew_size` /
`recv_day_seconds`. No one value puts all four leaves in the binding-but-stable band: on the
passing config fulfillment binds hard (46-60 of 75 drains) and store lightly (14-17). **The
campaign is therefore mostly a FULFILLMENT experiment**, and that should be said out loud
rather than discovered in the results.

The same asymmetry lands on the fee axis. Overage share by threshold, passing config:

| threshold | lt0 ful | ltrand ful | lt0 store | ltrand store |
|---|---|---|---|---|
| 1 d | 85.2% | 96.5% | 95.6% | 96.8% |
| 2 d | 41.3% | 62.5% | 93.0% | 95.0% |
| 3 d | 16.5% | 40.8% | 87.9% | 88.9% |
| 5 d | 0.0% | 1.8% | 82.4% | 85.8% |
| 7 d | 0.0% | 0.0% | 47.0% | 60.1% |
| 10 d | 0.0% | 0.0% | 14.4% | 21.1% |

"Nonzero, non-saturated" lands at **2-3 days for fulfillment and 7-10 days for store** — 3.5x
apart. 07's derive-late decision saves the REPORT (stamps are raw, so each channel can be
re-reported at its own threshold with no re-simulation) but does NOT save `gain_gated`: the
urgency GATE reads `INBOUND_FEE_THRESHOLD_DAYS` at SIMULATION time, and 08 derives the H grid
from it as multiples. One global threshold makes `gain_gated`'s three H cells meaningful in one
channel and degenerate in the other.

**Recommendation: `PHASE2_THRESHOLD_DAYS = 3.0`** — fulfillment-calibrated, because fulfillment
is the channel that actually binds; store's fee axis is re-reported at 7-10 d at analysis time,
and `gain_gated`'s H grid is read as a fulfillment result only.

### 5. The values for `whatif_config`

Confirmed and unchanged: `PHASE2_LEAD_MINUTES = 480.0`, `PHASE2_LEAD_SPREAD = 0.7`,
`PHASE2_DOCK_DOORS = 4`, `PHASE2_FINITE_W = 5`. The lead shape does its job — the yard ranks by
arrival rather than dispatch, and contention is present at every leaf. Changed:
`PHASE2_THRESHOLD_DAYS` 2.0 -> 3.0. NEW, and not currently expressible in
`phase2_inbound_axis` because they are run-level: `--recv-crew-size 4 --recv-day-seconds 43200`
must ride phase 2's command line. Both ARE recorded in `run_spec.json` (`run_analysis` reads
them), so a phase-2 run stays re-analysable — but they are the whole experimental condition,
and carrying them on a command line rather than in the spec is a reproducibility seam worth
closing.

### 6. The `'all'`-window multiplier: MEASURED. The wall-clock bench: NOT TAKEN.

08 called drains-per-batch "the unmeasured quantity" and predicted it was the real multiplier
on the futuresight window's n^2. **It is 1.00** — measured on every leaf of every run here, and
structurally guaranteed: `strategy_runner` calls `mgr.check_reorders` exactly once per batch
iteration. `_receive_standing` then makes TWO entry calls per drain (`yard_order`,
`dock_order`), so an arm naming `futuresight` on both knobs aggregates twice per batch, not
"n^2 x a large multiplier". The aggregation term is 2n against the copy term's n — same order —
so the `demand_v` memo 06 licensed would save roughly two thirds of the futuresight overhead
rather than rescuing an unaffordable arm. **`'all'` is affordable; the memo is not a
prerequisite.**

NOT done: an actual wall-clock / `t_reord` bench of a futuresight arm at depth. It needs its own
75-batch run and it is a phase-2 GRID-COST question, not a validity one — it can be taken from
phase 2's own first futuresight cell. Named here rather than quietly dropped.

### 7. Cost facts for whoever runs the campaign

At published depth and full catalogue one work unit is ~1,230-1,320 s wall (16 units on 12
workers ~= 70 min; peak RSS ~5.5 GB per worker, so worker count is RAM-bound before it is
CPU-bound). Phase 2's 480 units therefore land near 13 hours of simulation on this machine —
corroborating 08's ">twelve hours" independently. Warehouse build ~72 s and stocking ~62 s per
unit on top.

### 8. Verification that rode along

- 17's whole `yard` family renders on a real standing-yard run at published depth — four
  evaluations; `absolute_yard_scorecard` read 11,073 trailers / 1,955 censored / 4 doors /
  73% door utilization / depth 769.9 max 2208 / 58 of 75 drains bound. The run-end censored
  flush works: standing trailers appear rather than vanishing.
- `throughput.missed` renders, which is what let the saturation call be made on a number
  rather than an impression.
- Conservation held on every arm of both 75-batch runs.

## Comments

2026-09-01, on resolution: findings 3 and 4 share one root cause — departments whose capacities
were never calibrated against one another — and it sits UPSTREAM of this campaign rather than
inside it. Raised by the map's owner in the same session and routed out of this map's scope;
see the map's Out-of-scope entry and its successor effort.
