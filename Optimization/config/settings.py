"""settings — every tunable a run has, as a flat list of named values.

The authoring surface. One name per knob, at module level, grouped by what it governs and
annotated with what changing it costs. `sim_config.CONFIG` is DERIVED from this file, so
this is the one place a value is written down and the nested dict is a view of it.

    settings.py          you edit this                        (declaration)
        |
        v
    sim_config.CONFIG    the nested dict every reader knows   (run state)
        |
        v
    resolve at run time  + CLI flags + the what-if cell       (this run)

## Reading this file

Every value below is a DEFAULT. Three things override it, in order:

1. a CLI flag (`--n-batches`, `--seed-world`, …) — see `run_simulation`
2. a what-if cell, for the three axes it sweeps — see `simdriver.cells`
3. a `--resume`, which restores whatever the original run recorded in its run spec

Overrides are written into `CONFIG` and read back through the accessors in `sim_config`
(`n_batches()`, `seed_world()`, …). **Never snapshot one of these at import**: a module
scalar cannot see a CLI override, which is a bug this project has now shipped twice — once
for `_INITIAL_FILL`, which made a run misreport its own sizing in its warehouse DB, and once
for five more that were caught before they could.

## What does NOT live here

- **Pick-config coefficients.** Those are a swept AXIS, not a setting: each variant is a
  self-registering module under `Optimization/simconfig/configs/`, and the run sweeps all of
  them. A number that varies within one run is not a default.
- **What-if cell definitions.** `Optimization/config/whatif_config.SPECS`.
- **Restock-rule membership.** `Optimization/config/strategies`.
- **Anything derived.** `checkpoint_every` is computed from `n_batches`; the shift's
  hour-length is `SHIFT_SECONDS / 3600`. A derived value declared beside its input is two
  things to keep in step.

Adding a setting: declare it here, thread it into `CONFIG` below, and — if it changes
results — give it a CLI flag, record it in the run spec, and restore it in both
`_apply_run_spec` and `run_analysis._apply_run_shape`. Miss one of those four and the flag
is accepted and then silently ignored; `Tests/unit/test_run_shaping_params.py` is the guard.
"""
from __future__ import annotations

from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS

# ── determinism ──────────────────────────────────────────────────────────────────
# The two values that decide whether two runs are COMPARABLE. Same world seed = same
# warehouse and catalogue; same batch seed = same demand stream. Changing either makes a
# run incomparable with every run before it, which is why both have CLI flags and both are
# recorded in the run spec.

SEED_WORLD = 42            # warehouse + catalogue construction
SEED_BATCHES = 1337        # base of the batch stream; each channel adds its own offset

# ── run shape ────────────────────────────────────────────────────────────────────

N_BATCHES = 100            # batches per arm.  --n-batches; 0 is rejected, not ignored
WORKERS = 1                # pool size.  Changes wall-clock only, never a result
MAX_SKUS = None            # global input-catalog cap; None = no cap.  Preserves the mix
CHECKPOINT_FRAC = 0.1      # flush every ceil(N_BATCHES * this) batches
KEYFRAME_INTERVAL = 25     # batches between viewer keyframes.  Audit points, not accuracy

# Batch-sampler VERSION, and therefore a RESULTS ERA rather than a tuning knob: v2 runs are
# not row-comparable with the pre-flip archive.  Batch caches are fingerprinted per sampler
# so the two eras cannot contaminate each other.  `--sampler v1` is the escape hatch.
SAMPLER = 'v2'

# ── the clock ────────────────────────────────────────────────────────────────────
# A REPORTING FRAME over a continuous clock, not a scheduler.  It labels
# `work_events.shift_index`; nothing dispatches against it, work does not pause at the
# whistle, and a task spanning a boundary is recorded under the shift it started in.

SHIFT_SECONDS = DEFAULT_SHIFT_SECONDS      # 8 hours, in the sim's own unit

# ── the working day ──────────────────────────────────────────────────────────────
# A SCHEDULER, and the opposite of SHIFT_SECONDS above in every way that matters: this one
# dispatches.  Kept as three separate settings because they answer three questions and a run
# can want any one without the others.
#
# The defaults reproduce the pre-working-day runner exactly, which is the property that lets
# every one of these ship without re-deriving the archive.

WORK_DAY_SECONDS = None    # day length; None = SHIFT_SECONDS.  --work-day-seconds
RELEASES_PER_DAY = None    # batches released per day; None = CONTINUOUS, i.e. batch i starts
                           # when batch i-1 finished, which is what the runner already did.
                           # An integer cuts the day into that many slots, and an EMPTY batch
                           # then consumes one -- which a clock driven by makespans cannot
                           # express.  --releases-per-day
CUT_AT_DAY_END = False     # stop pickers at the whistle and roll their unreached work into
                           # the next batch.  Changes WHICH units are picked in WHICH batch,
                           # so it can never be a silent default.  --cut-at-day-end
# ── the receiving crew (inbound) ───────────────────────────────────────────────────
# Inbound is its OWN crew, with its own hours: merchandise whose lead time has elapsed lands
# on a dock and a receiving crew works through it, rather than appearing in a put queue for
# free.  Off by default and STRUCTURALLY so -- size 0 means `recv_crew_spec()` returns None,
# so no dock, no crew and no clock is ever constructed and the run is byte-identical.
#
# There is deliberately no RECV_CREW_MODE and no receiving speed table.  An unload has no
# travel term (there is no dock coordinate anywhere in the model), so there is nothing for a
# speed to scale: this model cannot say whether a forklift crew unloads faster than a hand
# crew, only how crew SIZE moves a makespan, because size is the number of clocks.  Four
# constants nothing reads would imply otherwise.  See Warehouse/operations/unload.py.
RECV_CREW_SIZE = 0         # receivers; 0 = NO receiving crew, i.e. every run before this
                           # existed.  --recv-crew-size
RECV_DAY_SECONDS = None    # the receiving crew's own day, in seconds; None = no whistle, so
                           # the dock drains every batch and the crew only ever costs
                           # seconds.  Deliberately NOT tied to --cut-at-day-end: that knob
                           # changes which units are PICKED in which batch, and coupling
                           # would make receiving rollover observable only in a configuration
                           # that also perturbs picking.  --recv-day-seconds
RECV_DAY_ORIGIN = 0.0      # when the receiving day starts on the arm's absolute axis.  A
                           # dock that opens before the pickers is a real shift pattern and
                           # this is where it goes.  --recv-day-origin

ROLL_OVER_UNPICKED = False # demand a batch did not pick joins the NEXT batch's demand,
                           # whatever the cause: the day cut, a bin that held less than the
                           # plan, or no bin holding the SKU at all.  The largest behaviour
                           # change in this family -- it ends comparability with the whole
                           # archive and makes later batches bigger, so it is opt-in even
                           # though it is the modelling we want.  --roll-over-unpicked

# ── crews ────────────────────────────────────────────────────────────────────────
# Imported from simconfig.constants rather than restated: the self-registering pick-config
# modules need these too and importing sim_config from them would cycle.

from Optimization.simconfig.constants import _FF_PICKERS, _STORE_PICKERS  # noqa: E402

STORE_PICKERS = _STORE_PICKERS         # machine order-picker pool
FF_PICKERS = _FF_PICKERS               # human-walker pool

# The MODE each pick pool works in.  These were implicit in the pool names
# ('store_machine', 'fulfillment_walker') and in constants.py's comments for years
# before anything read them; declaring them here is what finally makes them reach a run.
STORE_PICK_MODE = 'machine'            # order-picker
FF_PICK_MODE = 'foot'                  # walker
PUT_CREW_SIZE = 1                      # one walker; put-away is not yet a swept axis
PUT_CREW_MODE = 'foot'                 # 'foot' | 'machine' -- picks the speed below

# ── the SPLIT put-away configuration ──────────────────────────────────────────────
# Off by default: one catch-all queue named 'all', which is what every run has ever used.
# On, put-away becomes three streams with their own crews, carts and floor space --
# singletons into a cart, pallets onto a forklift, fulfillment into its own bins.
#
# Splitting is not free and not neutral.  Each queue gets its OWN crew, so three queues of
# size N is 3N putters and roughly 3x the throughput; size them against the single-queue
# total or the comparison is meaningless.  And the staging limits below are what make the
# split interesting at all: with no limit the floor is unbounded, nothing is ever refused,
# and the backpressure machinery (held items, the refill loop, `blocked`) never executes.
PUT_QUEUE_SPLIT = False    # three streams instead of one.  --put-queue-split
PUT_CART_CREW = 1          # putters walking singletons into carts.   --put-cart-crew
PUT_PALLET_CREW = 1        # forklift drivers moving pallets.         --put-pallet-crew
PUT_FF_CREW = 1            # putters on the fulfillment stream.       --put-ff-crew
PUT_CART_STAGING = None    # items the singleton floor holds at once; None = unbounded.
                           # --put-cart-staging
PUT_PALLET_STAGING = None  # pallet positions on the dock floor.  The one most worth
                           # setting: with nowhere to lay pallets out there is nothing to
                           # re-sort, which is what makes pallet put-away FIFO physical
                           # rather than stipulated.  --put-pallet-staging
PUT_FF_STAGING = None      # fulfillment floor space.  --put-ff-staging
PUT_SWAP_COEF = 0.0        # seconds to swap a full put-away cart for an empty one.  0 keeps
                           # swaps counted and free, which is what the single-queue default
                           # does today.  --put-swap-coef

# Travel speeds are a (role x mode) table.  The PICK half lives in the pick-config
# registry, correctly: those coefficients are a swept AXIS, and a number that varies
# within one run is not a default.  The PUT half has no other home, so it is here.
#
# UNCALIBRATED.  Nothing has measured put-away travel separately, so each mode is set to
# the magnitudes the pick side uses for the same mode -- a putter walks like a walker and
# drives like an order-picker until someone measures otherwise.  Two consequences worth
# knowing: put-away durations are a MODEL, not a measurement, and the foot profile
# inherits the pick side's odd lift speed (see the note on the walker out-lifting the
# machine in Warehouse/operations/README.md).
PUT_FOOT_X = 2.0                       # ft/s along the aisle
PUT_FOOT_Y = 4.0                       # ft/s vertical
PUT_MACHINE_X = 3.0
PUT_MACHINE_Y = 2.0

# ── per-channel storage and demand ───────────────────────────────────────────────
# Store and fulfillment are INDEPENDENT sections of one warehouse: each sweeps its own
# pick-configs, runs its own restock suite, and writes its own DB subtree.  They are
# combined only after analysis, by run_channel_rollup.

STORE_CART = 'StoreCart'
FF_CART = 'FulfillmentCart'

STORE_FILL = 0.85                      # sizing headroom; --store-fill
FF_FILL = 0.85                         # --ff-fill

STORE_BATCH_MEAN = 0.15                # batch size as a fraction of the channel's SKUs
STORE_BATCH_STD = 0.05
FF_BATCH_MEAN = 0.20
FF_BATCH_STD = 0.05

# Velocity zoning: the fulfillment experiment axis, off by default.  A cell turns it on.
ZONING_OFF = {'enabled': False, 'n_bands': 3, 'mode': 'equal',
              'abc': {'mass_thresholds': [0.7, 0.9]}}
