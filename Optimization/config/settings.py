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
  hour-length is `REPORTING_FRAME_SECONDS / 3600`. A derived value declared beside its input is two
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

REPORTING_FRAME_SECONDS = DEFAULT_SHIFT_SECONDS   # 8 hours, in the sim's own unit
#   (SHIFT_SECONDS until the convention pass: renamed so *shift* is free for the
#   drain-or-cap scheduler that actually dispatches; this one only labels
#   work_events.shift_index — logical name frame_index.)

# ── the working day ──────────────────────────────────────────────────────────────
# A SCHEDULER, and the opposite of REPORTING_FRAME_SECONDS above in every way that matters: this one
# dispatches.  Kept as three separate settings because they answer three questions and a run
# can want any one without the others.
#
# The defaults reproduce the pre-working-day runner exactly, which is the property that lets
# every one of these ship without re-deriving the archive.

WORK_DAY_SECONDS = None    # day length; None = REPORTING_FRAME_SECONDS.  --work-day-seconds
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

# ── the inbound trailer pipeline ─────────────────────────────────────────────────
# OFF unless a trailer type is named, and STRUCTURALLY so: `inbound_spec()` returns None,
# no TrailerTransit is built, and the manager keeps the batch lead queue byte-identically.
# Naming a type is a RESULTS ERA, not a tuning knob -- reorders then travel as trailer
# loads, pack per trailer portion, and admit with source 'trailer'.
INBOUND_TRAILER_TYPE = None      # '53' (26 pallet positions) | '28' (12); None = no trailers
INBOUND_DOCK_DOORS = 4           # staging slots at the dock; BOOKKEEPING in v1 -- every
                                 # arrival lands at the batch epoch and every policy is
                                 # FIFO, so a throttle here would invent staffing physics.
                                 # The knob exists for the policies that make doors bite.
INBOUND_LEAD_MINUTES = 0.0       # the MEDIAN per-trailer transit delay, authored in MINUTES
                                 # (converted once at the spec seam); 0 = arrives instantly
INBOUND_LEAD_SPREAD = 0.0        # sigma of the lognormal around that median, DIMENSIONLESS:
                                 # lead_i = median * exp(sigma * Z_i), one stateless draw per
                                 # trailer keyed by (SEED_WORLD, tag, seq).  Leads are a WORLD
                                 # fact every arm shares, like aisle geometry -- hence no seed
                                 # knob of its own, and trailer #N draws the same lead in every
                                 # arm of a run (common random numbers, for free).
                                 # 0.0 = NO draw at all: no RNG is constructed and the scalar
                                 # path runs verbatim, so the default is byte-identical by
                                 # CONSTRUCTION rather than by argument.
                                 # > 0 REQUIRES INBOUND_STANDING_YARD and a non-zero median;
                                 # both refuse loudly at inbound_spec, because v1's dock ranks
                                 # by dispatch seq (a spread would half-work -- arrival-batch
                                 # shifts visible, order scrambling invisible) and a spread
                                 # over a zero median degenerates to constant zero.
INBOUND_GLOBAL_POLICY = 'fifo'   # trailer order at BOTH dock moments (Inbound/priorities.py)
INBOUND_LOCAL_POLICY = 'fifo'    # load-pallet order within a trailer
INBOUND_TRAILER_BOUND = None     # the dock's k_cap analog, in TRAILERS; None = unbounded
                                 # (inert under fifo -- shipped for the interface, by decision)

# ── the standing yard ────────────────────────────────────────────────────────────
# Doors become REAL: at most INBOUND_DOCK_DOORS trailers staged, a trailer holds its door
# across drains until fully unloaded, the yard-pull fires when a door frees, and the split
# yard/dock priorities replace the single global ranking.  OFF = v1's drain-everything
# release(), byte-identical by construction (the v1 transit class binds untouched).
# Requires a trailer type AND a receiving crew -- either missing FAILS LOUDLY at spec
# build (inbound_spec), never silently inert: a standing yard nobody can unload would
# defer its merchandise forever without one error message.
INBOUND_STANDING_YARD = False    # the one flag gating the whole standing model
INBOUND_CREW_ALLOCATION = 'split'  # 'split' = door teams (the standing physics: workers
                                 # dealt across staged trailers in dock-priority order,
                                 # doors free staggered); 'merged' = v1's pooled gang,
                                 # kept as honest physics and the lockstep bridge.
                                 # A mechanics MODE, not a policy registry.
INBOUND_YARD_POLICY = 'fifo'     # freed door <- which standing trailer (YARD_POLICIES)
INBOUND_DOCK_POLICY = 'fifo'     # crew <- which staged trailer     (DOCK_POLICIES);
                                 # under door teams this is a worker-ALLOCATION preference
# The two days-denominated knobs ("Name the policy arms", 05).  Labor-hours and fee-days
# never blend into one scalar anywhere: the fee side is only ever the yes/no urgency test
# these two express, and the SAME threshold feeds the yard fee report -- one knob, two
# readers, so the gate and the metric can never disagree about "overdue".
INBOUND_FEE_THRESHOLD_DAYS = 2.0    # free yard days before a trailer accrues overage;
                                    # 2.0 is a stated placeholder -- calibration rides the
                                    # funnel, not this default
INBOUND_URGENCY_HORIZON_DAYS = 0.0  # gain_gated's only dial: trailers within this many
                                    # days of crossing the threshold are served FIFO ahead
                                    # of the plan.  0 ~ pure gain (only already-overdue
                                    # trailers jump); >= threshold = pure FIFO
INBOUND_FUTURESIGHT_BATCHES = None  # the futuresight arm's window, in SCRIPT BATCHES ahead
                                    # of the one being released; 'all' = the oracle w=inf (a
                                    # string sentinel that survives a run spec honestly);
                                    # None = inert.  The arm REQUIRES it set and requires the
                                    # precomputed batch script -- refusal-until-clean on both
                                    # ("Define the inbound objective", 10; the entry itself
                                    # is a declared-unlawful upper-bound REFERENCE, never in
                                    # the recommendable set)
# The unload cost's own coefficients -- the independent inbound price lever.  None = the
# put-away value BY REFERENCE (Inbound/unload.py's UnloadCost defaults), so every existing
# run is byte-identical and no era splits; a number = this dock's own price.  Same
# functional form either way: a second invented shape is the drift this repo paid for
# twice (see UnloadCost's docstring).
INBOUND_UNLOAD_INTERCEPT = None
INBOUND_UNLOAD_WEIGHT_COEF = None
INBOUND_UNLOAD_VOLUME_COEF = None

SHIFT_DRAIN_OR_CAP = False # THE CALIBRATED ERA (--shift-drain-or-cap).  One site-wide
                           # working stretch that ends when no
                           # standing work remains and none is still scheduled to release,
                           # or at the cap, whichever comes FIRST.  The cap REUSES the day
                           # length (WORK_DAY_SECONDS, falling back to the reporting frame)
                           # -- no second duration to reconcile -- and capping IMPLIES the
                           # cut/carry semantics: work standing at the whistle rolls via the
                           # existing carryover machinery, because a cap without carry loses
                           # demand.  One boundary for every crew when on (the receiving
                           # day's own knobs apply only flag-off); days stay ORIGIN-ALIGNED
                           # on the absolute clock -- an early drain stops the labour, never
                           # the calendar.  Off = today's behaviour exactly.  ON IMPLIES the
                           # era: one release per day (RELEASES_PER_DAY 1), the cut, and the
                           # roll-over, and the put and receiving crews are DERIVED from the
                           # pickers rather than declared (Optimization/simconfig/staffing.py).

ROLL_OVER_UNPICKED = False # demand a batch did not pick joins the NEXT batch's demand,
                           # whatever the cause: the day cut, a bin that held less than the
                           # plan, or no bin holding the SKU at all.  The largest behaviour
                           # change in this family -- it ends comparability with the whole
                           # archive and makes later batches bigger, so it is opt-in even
                           # though it is the modelling we want.  --roll-over-unpicked

# ── crews ────────────────────────────────────────────────────────────────────────
# THE ONE DECLARED STAFFING INPUT of the calibrated era (.scratch/department-calibration,
# "Define the calibrated era"): pickers per channel.  Every other crew -- the put crew, the
# receiving crew -- is a site total DERIVED from these, so these two are the only headcounts
# a run may declare.  Imported from simconfig.constants rather than restated (that leaf is
# importable from below sim_config, which is where the staffing records live); the values
# there are these knobs' DEFAULTS and nothing else, and are read at CALL time through
# `CONFIG['global']['store_pickers']` / `['ff_pickers']` and `sim_config.channel_pickers`.

from Optimization.simconfig.constants import _FF_PICKERS, _STORE_PICKERS  # noqa: E402

STORE_PICKERS = _STORE_PICKERS         # machine order-picker pool.  --store-pickers
FF_PICKERS = _FF_PICKERS               # human-walker pool.          --ff-pickers

# The MODE each pick pool works in.  These were implicit in the pool names
# ('store_machine', 'fulfillment_walker') and in constants.py's comments for years
# before anything read them; declaring them here is what finally makes them reach a run.
STORE_PICK_MODE = 'machine'            # order-picker
FF_PICK_MODE = 'foot'                  # walker
PUT_CREW_SIZE = 1                      # one walker.  FLAG-OFF ONLY: under the calibrated
                                       # era the put crew is DERIVED (a site total sized
                                       # from the pickers' daily demand) and passing this
                                       # explicitly is an error.  --put-crew-size
PUT_CREW_MODE = 'foot'                 # 'foot' | 'machine' -- picks the speed below.  A
                                       # DECLARED staffing input under the era: the
                                       # derivation sizes the count, the mode is a
                                       # labour-model term the cost model prices.
                                       # --put-crew-mode

# ── the calibrated era's declared scalars ───────────────────────────────────────
# Every step of the staffing derivation (Optimization/simconfig/staffing.py) is a declared
# scalar, and every default here is an ASSUMPTION of the era, never a measurement
# (.scratch/department-calibration, "Define the calibrated era"; "Declare the equilibrium
# bands").  They are staffing INPUTS: recorded with the run under `staffing.inputs`,
# restored on resume and re-analysis, carried in the worker payload, and stamped onto
# sim_result -- by construction, because they ride `sim_config.STAFFING_KEYS`.
RHO_PICK = 0.85                        # picking utilization target: worked / granted.
                                       # Capacity = K x S x rho.  --rho-pick
RHO_PUT = 0.85                         # put-away utilization target.  --rho-put
RHO_RECV = 0.85                        # receiving utilization target.  --rho-recv
F_PUT = 1.0                            # units put per unit picked; 1.0 = steady state
                                       # (what is picked is replenished).  --f-put
F_RECV = 1.0                           # packs received per pack the script implies; 1.0 =
                                       # steady state.  --f-recv
BAND_TOL = 0.10                        # |realized - expected| utilization tolerance, one
                                       # absolute number for all three departments.
                                       # --band-tol

# ── the expected constants' OVERRIDES ───────────────────────────────────────────
# None = take the closed-form expectation over the catalogue and the built geometry
# (Optimization/simconfig/expected_travel.py, computed at setup); a number is seconds per
# unit, recorded `declared`, and the expectation is still recorded beside it.
S_PICK_STORE = None                    # seconds per unit picked, store.  --s-pick-store
S_PICK_FF = None                       # seconds per unit picked, fulfillment.  --s-pick-ff
S_PUT = None                           # seconds per unit put away, one site value.  --s-put

# ── the other crews' PRICE, as scalars of the pickers' ──────────────────────────
# Put-away and receiving keep picking's cost shape and picking's coefficients BY REFERENCE
# (Warehouse/operations/putaway.py, Inbound/unload.py); these three scalars are the ONLY
# place their numbers may differ.  The defaults are the kernel's declaration
# (Warehouse/kernel/cost_model.py), imported rather than restated so the dataclass
# defaults and the run defaults cannot drift apart; every one is a declared ASSUMPTION of
# the calibrated era (.scratch/department-calibration, "Define the calibrated era"), not a
# measurement.
from Warehouse.kernel.cost_model import (  # noqa: E402
    DEFAULT_PUT_INTERCEPT_SCALE, DEFAULT_PUT_ITEM_RATIO, DEFAULT_RECV_INTERCEPT_SCALE)

PUT_INTERCEPT_SCALE = DEFAULT_PUT_INTERCEPT_SCALE    # put intercept = picking's × this
                                                     # ("putting is less work").
                                                     # --put-intercept-scale
PUT_ITEM_RATIO = DEFAULT_PUT_ITEM_RATIO              # put per-item charge = picking's × this.
                                                     # --put-item-ratio
RECV_INTERCEPT_SCALE = DEFAULT_RECV_INTERCEPT_SCALE  # receive intercept = PUT-AWAY's × this;
                                                     # the per-item charge is put-away's,
                                                     # charged once per PACK.
                                                     # --recv-intercept-scale

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
