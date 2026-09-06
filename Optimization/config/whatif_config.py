"""Experiment definition for `run_simulation --whatif`: an aisle-reconstruction × velocity-zoning ×
picker-scheduler matrix.

Editing + committing this file is how a what-if sweep is DEFINED — the same way the strategies sweep
lives in `strategies.CHANNEL_RESTOCKS` and the base warehouse knobs live in `sim_config.CONFIG`.
`run_simulation` consumes `WHATIF`; `--whatif` is just the flag that turns it on.  The matrix
simulates the SAME frozen inventory + SAME batch stream across every cell, so cells differ ONLY in
the swept knobs — an apples-to-apples comparison.

Cells are the combinatorial product schedulers × zoning × ks × losses (loss collapses to 0 when
k == 1), named `k{k}[_l{loss%}]_{zone}[_{sched}]`.  The scheduler suffix (`_rr` / `_lpt`) is added
only when more than one scheduler is swept; the (k=1, off, round_robin) cell is the reference.

CURRENT SCENARIO — picker-scheduler A/B with NO ABC aisle splitting and NO ABC velocity zoning: hold
the layout fixed (whole aisles, no A/B/C banding) and sweep the task→picker scheduler `round_robin`
(naive static i%num_pickers) vs `lpt` (makespan-MINIMIZING load balancer) across the full
assignment-function suite — measuring the throughput (batch-makespan) gain of load-balancing at
unchanged task makespan (total labor).  The ABC aisle-split and ABC velocity-zoning axes are preserved
below as commented-out lines; uncomment them to sweep layout too.
"""

WHATIF = {
    # ── ABC aisle splitting: OFF (k=1 = whole aisle, no A/B/C segmentation) ───────────────
    'ks':     [1],               # aisle_split segment counts; 1 = no split (ABC splitting OFF)
    'losses': [0.0],             # capacity_loss per cut; applies ONLY when k > 1
    # 'ks':     [1, 2, 3],       # ← ABC aisle splitting ON: 1 = whole aisle, 2/3 = segments per aisle
    # 'losses': [0.0, 0.1],      # ← capacity lost per cut when k > 1

    # ── ABC velocity zoning: OFF ─────────────────────────────────────────────────────────
    'zoning': [                  # (cell-name-suffix, velocity_zoning spec)
        ('off', {'enabled': False}),
        # ('abc', {'enabled': True, 'n_bands': 3, 'mode': 'abc',   # ← ABC A/B/C demand-mass banding
        #          'abc': {'mass_thresholds': [0.7, 0.9]}}),
    ],

    # ── Picker-scheduler axis (the ACTIVE sweep): naive vs minimizing ─────────────────────
    # 'round_robin' = the naive static scheduler (legacy i%num_pickers, no load balancing);
    # 'lpt'         = the makespan-MINIMIZING scheduler (heavy-first least-loaded load balancer).
    # >1 value adds a `_rr`/`_lpt` cell-name suffix; a single value = no suffix.
    'schedulers': ['round_robin', 'lpt'],

    # Assignment-function (restock) arms to sweep in every cell.  'all' = the full suite
    # (CHANNEL_RESTOCKS = None); a list/tuple of restock keys = a subset; None = leave
    # strategies.CHANNEL_RESTOCKS exactly as already committed (don't override it here).
    'arms': 'all',
    'reference': 'k1_off_rr',    # naive (round_robin) baseline; run_whatif_delta diffs k1_off_lpt vs it
}


# ── the inbound funnel: phase 1 selects restock rules, phase 2 sweeps inbound policy ──
# Two specs for one campaign ("Design the phased funnel", `.scratch/inbound-optimization`).
# Phase 1 is a FRESH, inbound-OFF run whose only job is to rank the 17 restock rules on total
# production hours; no phase-1 number is ever published, which is what makes a between-phase
# build legal.  Phase 2 is the ten-cell inbound matrix over the rules phase 1 chose.

#: The phase-2 arm set: the restock keys `run_restock_selection` chose, plus its mandatory
#: `fifo` rider.  None until phase 1 has run — `_run_whatif_matrix` REFUSES an inbound matrix
#: with no arm set rather than falling through to the committed full suite, which would be
#: 34 arms x 10 cells instead of 12 x 10 and would not be the funnel at all.
PHASE2_ARMS = None

#: The arrival regime and the two grids phase 2 sweeps against.  Every one of these is a PILOT
#: output, not a preference: the pilot fixes the free-days threshold (where overage is nonzero
#: and non-saturated), the lead shape that produces yard contention at all, and the finite
#: window worth benching against the oracle.  SET BY THE PILOT GATE (`inbound_pilot` spec,
#: `.scratch/inbound-optimization/issues/22-run-the-pilot-gate.md`) at published depth on the
#: full catalogue; the H points move with the threshold because they are derived from it
#: rather than typed beside it.
#:
#: THE THRESHOLD IS FULFILLMENT-CALIBRATED AND THAT IS A COMPROMISE, not a measurement that
#: came out clean.  "Nonzero, non-saturated" lands at 2-3 days in fulfillment and 7-10 days in
#: store -- 3.5x apart, because the two channels' receiving loads differ 7.4x against one
#: global crew knob.  3.0 follows the channel that actually binds (46-60 of 75 drains, against
#: store's 14-17).  The fee REPORT survives this: 07 stores stamps raw, so store is re-reported
#: at its own threshold with no re-simulation.  `gain_gated` does not -- its urgency gate reads
#: this value at SIMULATION time, so its three H cells are a FULFILLMENT result and are
#: degenerate in store.  Read them that way.
PHASE2_THRESHOLD_DAYS = 3.0
PHASE2_LEAD_MINUTES = 480.0        # ~ one working day, the first probe 02 named — confirmed:
                                   # the yard ranks by arrival, not dispatch, at every leaf
PHASE2_LEAD_SPREAD = 0.7
PHASE2_DOCK_DOORS = 4
PHASE2_FINITE_W = 5

#: The RECEIVING regime phase 2 must run under, and the reason it is not in the inbound axis:
#: `recv_crew_size` / `recv_day_seconds` are `CONFIG['global']` knobs with no cell axis, so they
#: ride the COMMAND LINE.  They are nonetheless the whole experimental condition -- the pilot's
#: first attempt used a physically plausible dock (2 receivers, an 8-hour day) and starved the
#: warehouse to a 49.6% missed share, because the crew is granted one day-REMAINDER per BATCH
#: while a store batch spans 19 working days.  Effective capacity is crew x day / 2 per batch;
#: size it against the BATCH, never against a shift.  Both are recorded in the run spec, so a
#: run stays re-analysable, but a phase-2 launch that forgets them is not the pilot's regime.
PHASE2_RECV_CREW_SIZE = 4
PHASE2_RECV_DAY_SECONDS = 43200.0

#: H as MULTIPLES of the calibrated threshold, never absolute days — a horizon authored
#: independently of the threshold drifts out of meaning the moment the threshold is calibrated.
#: Both poles are omitted deliberately: H = 0 collapses `gain_gated` to `gain_forecast` (a seam
#: test, not a cell) and H >= the threshold collapses it toward FIFO.
PHASE2_H_MULTIPLES = (0.25, 0.5, 1.0)


def phase2_inbound_axis(*, threshold_days=PHASE2_THRESHOLD_DAYS,
                        lead_minutes=PHASE2_LEAD_MINUTES, lead_spread=PHASE2_LEAD_SPREAD,
                        doors=PHASE2_DOCK_DOORS, finite_w=PHASE2_FINITE_W,
                        h_multiples=PHASE2_H_MULTIPLES):
    """The ten-entry inbound axis for phase 2, as [(name_suffix, overrides), …].

    Every entry states EVERY key the axis touches, including the ones it is not exercising.
    That is not verbosity: `_apply_cell` mutates a process-wide CONFIG that is never reset
    between cells, so a key one cell writes and the next omits leaves the second running the
    first's policy under its own name.  `_inbound_axis` refuses a partial entry for exactly
    this reason, and stating the full record here is how that refusal is satisfied.

    The arrival regime rides the axis rather than the command line for the same reason: the
    inbound-OFF anchor must ALSO turn the lead spread off, because `inbound_spec()` refuses a
    spread without the standing yard — and it refuses it ABOVE the trailer-type early return,
    so an anchor inheriting a run-level `--inbound-lead-spread` would raise instead of running.
    Carrying the regime here makes the spec the whole experimental condition, reproducible
    without a remembered command line.

    The anchor is the validity check that phase 1's ranking transferred — present as a cell,
    never the reference.  The reference is `fifo`, so every delta reads "versus FIFO", which
    is the campaign's own question.
    """
    on = dict(trailer_type='53', dock_doors=doors, lead_minutes=lead_minutes,
              lead_spread=lead_spread, standing_yard=True,
              fee_threshold_days=threshold_days,
              urgency_horizon_days=0.0, futuresight_batches=None)

    def _policy(name, **over):
        return {**on, 'yard_policy': name, 'dock_policy': name, **over}

    axis = [
        ('fifo', _policy('fifo')),          # the reference AND the department-greedy fee pole
        ('lifo', _policy('lifo')),          # the adversarial control
        ('gmyopic', _policy('gain_myopic')),
        ('gforecast', _policy('gain_forecast')),
    ]
    for mult in h_multiples:
        axis.append((f'ggated_h{int(round(mult * 100)):03d}',
                     _policy('gain_gated', urgency_horizon_days=mult * threshold_days)))
    for w in (finite_w, 'all'):
        axis.append((f'fsight_w{w}', _policy('futuresight', futuresight_batches=w)))
    # The inbound-OFF anchor: no trailer type is the family's STRUCTURAL off switch, so the
    # manager keeps its batch lead queue and this cell is comparable with phase 1.
    axis.append(('inb_off', {**on, 'trailer_type': None, 'standing_yard': False,
                             'lead_minutes': 0.0, 'lead_spread': 0.0,
                             'yard_policy': 'fifo', 'dock_policy': 'fifo'}))
    return axis


# ── spec registry ────────────────────────────────────────────────────────────────
# Every run is a CELL matrix; a plain run is the single-cell spec ``single`` (cell ``k1_off``).
# ``run_simulation --spec <name>`` selects one.  This lightweight dict registry is the Phase-1
# seam; a later phase promotes it to a self-registering package like Performance_Evaluations.
SPECS = {
    # Plain run: one cell, no split/zoning, round-robin scheduler.  arms=None ⇒ leave
    # strategies.CHANNEL_RESTOCKS exactly as committed (the normal per-channel subset), so a
    # `--spec single` run is the old flat run nested under a single `k1_off` cell.
    'single': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['round_robin'], 'arms': None, 'reference': 'k1_off',
    },
    # The committed picker-scheduler A/B sweep (round_robin vs lpt over the full arm suite).
    'scheduler_ab': WHATIF,
    # ── the funnel, phase 1: rank the 17 restock rules, publish nothing ──────────────
    # ONE cell, inbound OFF, scheduler fixed at `lpt` — the series' winner; measuring on the
    # naive scheduler the series retired would rank for a configuration nobody runs.  A single
    # scheduler adds no name suffix, so the cell is `k1_off` and `is_reference` is False for it
    # (the predicate wants round_robin), which is why the reference is declared.
    'inbound_select': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['lpt'], 'arms': 'all', 'reference': 'k1_off',
    },
    # ── the funnel, phase 2: ten inbound policies over phase 1's chosen rules ────────
    # `arms` is PHASE2_ARMS: None until the selection artifact is read, and refused rather
    # than defaulted (see the constant).  Zoning stays off — the gain bundle refuses under
    # velocity zoning, so a zoned cell would fail at the first drain, not at spec build.
    'inbound_policies': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['lpt'], 'arms': PHASE2_ARMS,
        'inbound': phase2_inbound_axis(),
        'reference': 'k1_off_fifo',
    },
    # ── the funnel's PILOT GATE: one throwaway cell that decides whether the campaign runs ──
    # Deliberately NOT a phase-2 cell — the arm set is not known until phase 1 ends, so reuse
    # would be circular.  Its job is to answer 10's two acceptance criteria (yard contention
    # and binding cuts) under `fifo`, calibrate `INBOUND_FEE_THRESHOLD_DAYS`, and bench the
    # window; no pilot number is ever published.
    #
    # The inbound knobs ride the COMMAND LINE rather than an inbound axis, because the gate is
    # one arrival regime, not a sweep — and the receiving crew and its day, which are what
    # actually decide whether the yard binds, are run-level knobs with no cell axis at all.
    # The scheduler is pinned to `lpt` to match phase 2: the scheduler moves batch DURATION,
    # and batch duration is what decides how many leads elapse before the next drain observes
    # them, so a pilot on the retired scheduler would calibrate a different arrival regime.
    # Two rules, both in `Inbound.gain.FAITHFUL_GAIN_FAMILIES`: `fifo` is phase 2's mandatory
    # rider and its reference, `tmin` a ranked wave, so the gate is not read off one restock
    # shape.
    'inbound_pilot': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['lpt'], 'arms': ('fifo', 'tmin'), 'reference': 'k1_off',
    },
    # Schema-preflight canary: the SMALLEST spec that still produces a MULTI-cell tree (so the
    # cell level, `_frozen/`, and the cross-cell what-if outputs all appear).  Two cells x one
    # restock rule = 2 arms per cell instead of 34.  Not for analysis — runschema.preflight runs it
    # into a temp dir to prove the on-disk tree shape before a real simulation.
    '_canary_sweep': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['round_robin', 'lpt'], 'arms': ('fifo',), 'reference': 'k1_off_rr',
    },
    # The other half of the preflight pair: ONE cell, so `_frozen/` and the cross-cell what-if
    # outputs are absent.  Paired with a store-only catalog it also proves the `<channel>` level is
    # absent — the optionality that the `len(rel) < 4` relpath bugs used to get wrong.  Same
    # single-cell shape as 'single', but pinned to one restock rule (2 arms, not 34).
    '_canary_single': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['round_robin'], 'arms': ('fifo',), 'reference': 'k1_off',
    },
}


def get_spec(name):
    """Return the cell-matrix spec dict for a registered name (KeyError if unknown)."""
    return SPECS[name]
