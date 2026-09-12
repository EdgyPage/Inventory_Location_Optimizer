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

A spec also DECLARES ITS OWN SHAPE and is checked against it at `get_spec` — before the run
directory exists, before a worker is forked, before anything is written.  The checks live here
rather than in the driver because this file is where a campaign is authored, and every one of
them guards a defect whose only other symptom is a run that completes and means something else
(`validate_spec`).
"""
from Optimization.config.strategies import RESTOCK_KEYS   # the rule universe a spec is checked
                                                          # against — same-layer, derived from
                                                          # the grid, never a hand-kept copy

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

#: THE PHASE-2 RULE PAIRS: an ORDERED tuple of `(store_rule, fulfillment_rule)`, rank-aligned,
#: store first — `restock_selection.json`'s `rule_pairs.chosen`, copied here once phase 1 has
#: run.  None until then, and REFUSED rather than defaulted: falling through to the committed
#: full suite would be 34 arms x 10 cells instead of 12 x 10 and would not be the funnel at all.
#:
#: A phase-2 cell is a list of RULE pairs, not a flat arm set (site-dock 06 section 2).  The two
#: channels rank INDEPENDENTLY in phase 1 — their hour scales are not comparable, their receiving
#: loads differ 7.4x — and the diagonal pairs rank against rank.  The diagonal extends to
#: `Strategy.stock_mode`, so each rule pair is exactly two arm pairs (uni x uni, opt x opt) and
#: never four: `len(PHASE2_PAIRS) x 2` coupled units per (cell, inventory pair), the same arm
#: count as today's per-channel sweep fielded as half as many units each doing both leaves.
#:
#: THE RENAME FROM `PHASE2_ARMS` IS LOAD-BEARING, not cosmetic.  A flat arm tuple left behind
#: under the old name reads as a pair list whose first pair is the string `'fifo'` — a campaign
#: of one arm pair wearing the shape of a campaign of two arms.  Under the new name a stale flat
#: tuple is a SHAPE error at spec build (`validate_spec`); under the old one it was a silent run.
#:
#: `CHANNEL_RESTOCKS` is DERIVED from this list (`channel_restocks_for`), never authored beside
#: it, so the per-channel arm sets cannot drift from the pairing they exist to express.
PHASE2_PAIRS = None

#: The mandatory rider, as a PAIR.  `run_restock_selection` appends it if absent — outside k and
#: outside the extension cap — and a spec without it is refused.  That refusal is strictly
#: stronger than the flat `'fifo' not in arms` it replaces: it catches `fifo` ranked on ONE side
#: only, which a flat arm tuple cannot even express.  `uni_fifo`/`opt_fifo` are the analysis
#: baseline (`run_channel_rollup._baseline_entry` falls back to an ARBITRARY arm without one)
#: and the order-blind negative control, on BOTH leaves of a coupled unit.
PHASE2_RIDER = ('fifo', 'fifo')

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
#:
#: 2026-09-12: **3.0 IS DEGENERATE UNDER THE SITE DOCK AND MUST MOVE BEFORE THE CAMPAIGN RUNS**
#: ("Re-verify the gate under the lead-aware record").  The paragraph above is a leaf-model
#: artefact from end to end: it calibrated the threshold twice, once per channel, on two yards
#: that "Decide the contention regime under the derived crew" then retired.  One dock has ONE
#: detention distribution, and on the re-verification run no trailer of 609 was detained past
#: **1.837 days** -- so at 3.0 the overage is exactly zero on every arm, the fee axis is vacuous
#: and `gain_gated`'s H grid is derived from a number nothing can exceed.  The 3.5x channel
#: disagreement the paragraph calls a compromise does not exist any more; the threshold is a
#: measurement again.  Measured over the window population (313-314 trailers per arm, days
#: 20-39), trailers accruing any overage:
#:
#:      0.75 d  95-96%      1.20 d  38-43%      1.50 d   2-4%
#:      1.00 d  73-76%      1.25 d  28-33%      1.75 d   0-0%
#:      1.10 d  59-60%      1.30 d  19-25%      2.00 d     0%
#:
#: The knee is ~1.3 d: a quarter of trailers pay, the arms separate 1.7x (4.96 vs 8.61
#: trailer-days), and neither pole is saturated.  IT IS NOT COMMITTED HERE, deliberately.  The
#: gate that measured it FAILED on fulfillment's supply clause, so the run is not the regime the
#: campaign will run: closing that gap raises fulfillment's served units ~8%, and the ordered
#: units -- hence the dock's load -- follow.  Re-take the sweep on the passing run and set the
#: value THEN, from the table above rather than from a fresh search.
PHASE2_THRESHOLD_DAYS = 3.0
PHASE2_LEAD_MINUTES = 480.0        # ~ one working day, the first probe 02 named — confirmed:
                                   # the yard ranks by arrival, not dispatch, at every leaf
PHASE2_LEAD_SPREAD = 0.7
PHASE2_DOCK_DOORS = 4
#: THE DOOR-TEAM CAP, declared physics rather than a swept axis ("Decide the contention regime
#: under the derived crew", 3): at most ten receivers support one trailer's unload and pack at
#: once, every one additive.  It is what makes a door count mean anything -- with an uncapped
#: team the crew unloads at its full rate through ONE door and every other door is bookkeeping,
#: which is how a leaf-model yard came to read slack at any door count.  At 10 the site's 22
#: derived receivers need three doors to be fully dealt, so `PHASE2_DOCK_DOORS = 4` leaves the
#: CREW binding and the doors a ceiling -- the physical picture.  Held constant across the
#: matrix: it is not a policy, and sweeping it would confound the ordering comparison with a
#: capacity change.
PHASE2_DOOR_TEAM = 10
PHASE2_FINITE_W = 5

#: THE PILOT'S RECEIVING REGIME, kept as a RECORD of what the gate ran under and NOT a
#: recommendation.  `recv_crew_size` / `recv_day_seconds` are `CONFIG['global']` knobs with no
#: cell axis, so they rode the COMMAND LINE.  The pilot's first attempt used a physically
#: plausible dock (2 receivers, an 8-hour day) and starved the warehouse to a 49.6% missed share,
#: and this pair (4 receivers, a 12-hour day) was the size that made the yard bind -- because the
#: crew was granted one day-REMAINDER per BATCH while a store batch spanned 19 working days.  That
#: is the DENOMINATION INVARIANT violated (.scratch/department-calibration, charter): a
#: department's capacity must be denominated against the DAY the work arrives in, never against
#: a batch, and a per-batch grant drifts silently whenever a batch outlives a day.  Under the
#: calibrated era (`--shift-drain-or-cap`) the dock inherits the SITE's day, the receiving crew is
#: DERIVED from the pickers' daily demand, and passing either of these two flags explicitly is an
#: ERROR ("Design the staffing record", decision 4).  Flag-off they still work verbatim.  The
#: era-shaped `inbound_pilot` below carries NEITHER ("Verify the derived receiving crew").
PHASE2_RECV_CREW_SIZE = 4
PHASE2_RECV_DAY_SECONDS = 43200.0

#: THE CALIBRATED ERA as run-level defaults (.scratch/department-calibration, "Define the
#: calibrated era"): one site-wide drain-or-cap shift, one release per day, the cut and the
#: roll-over on.  A spec that names this under `run_defaults` runs under the era unless a flag
#: on the command line says otherwise (an explicit flag wins, with a note); the crews are then
#: DERIVED from the pickers (Optimization/simconfig/staffing.py) and the legacy crew flags are
#: an error.  Every campaign spec below carries it: the funnel holds until the era exists
#: ("Sequence the inbound funnel"), so a funnel launch without it is not the funnel.
ERA_RUN_DEFAULTS = {
    'shift_drain_or_cap': True,
    'releases_per_day': 1,
    'roll_over_unpicked': True,
    'cut_at_day_end': True,
}

#: THE PILOT GATE'S WHOLE REGIME as run-level defaults: the era, plus the arrival regime the
#: gate committed ("Run the pilot gate", 5) -- the same constants phase 2's inbound axis
#: reads, so the pilot and the campaign cannot drift apart.  Carrying them here rather than
#: on the command line closes the reproducibility seam 22 named: the gate re-runs as
#: `--spec inbound_pilot --n-batches 40` and nothing else.  NO crew key rides here, by
#: decision: under the era the receiving crew is derived and typing one is an error.
#:
#: AND COUPLING, since 2026-09-12 ("Re-verify the gate under the lead-aware record").  The gate
#: measures yard contention, and "Decide the contention regime under the derived crew" found the
#: per-leaf yard was an ARTEFACT of the independent-warehouse model: the regime the campaign runs
#: is the SITE's own dock, one yard and one receiving crew over both channels.  A pilot run per
#: leaf would re-measure the artefact that decision retired.  It rides here rather than on the
#: command line for the same reason every other key does -- phase 2 carries `couple_channels` in
#: PHASE2_RUN_DEFAULTS, and a regime the two specs state in DIFFERENT places is a regime they can
#: drift apart on.
PILOT_RUN_DEFAULTS = {
    **ERA_RUN_DEFAULTS,
    'couple_channels': True,
    'inbound_trailer_type': '53',
    'inbound_standing_yard': True,
    'inbound_dock_doors': PHASE2_DOCK_DOORS,
    'inbound_door_team': PHASE2_DOOR_TEAM,
    'inbound_lead_minutes': PHASE2_LEAD_MINUTES,
    'inbound_lead_spread': PHASE2_LEAD_SPREAD,
}

#: PHASE 2'S REGIME: the era, plus COUPLING.  A phase-2 cell is a pair of arms run as ONE site —
#: one dock, one receiving crew, one pool of putters over both channels — so the campaign runs
#: `--couple-channels` and the run root stamps `coupled: true` (site-dock 18).
#:
#: IT IS DECLARED HERE, on the RUN, and that is the whole point.  The charter's original rule was
#: "coupling rides the inbound flag"; site-dock 18 found it unbuildable, because site-dock 06
#: couples every cell INCLUDING the inbound-off pole, so no value of the inbound flag implies
#: coupling.  Riding `run_defaults` also makes the flag a spec DEFAULT rather than an override: a
#: `--couple-channels` typed on the command line agrees, and there is no way to type a flag that
#: silently turns the campaign back into twenty independent leaves — `_apply_run_defaults` notes
#: any explicit disagreement instead of swallowing it.
#:
#: Nothing else about the era changes: the crews are still derived per inventory pair and the
#: record is byte-identical across the coupling boundary (only how the crews are FIELDED moves),
#: which is why the campaign's staffing pin works unchanged across phases.
PHASE2_RUN_DEFAULTS = {
    **ERA_RUN_DEFAULTS,
    'couple_channels': True,
}

#: H as MULTIPLES of the calibrated threshold, never absolute days — a horizon authored
#: independently of the threshold drifts out of meaning the moment the threshold is calibrated.
#: Both poles are omitted deliberately: H = 0 collapses `gain_gated` to `gain_forecast` (a seam
#: test, not a cell) and H >= the threshold collapses it toward FIFO.
PHASE2_H_MULTIPLES = (0.25, 0.5, 1.0)


def phase2_inbound_axis(*, threshold_days=PHASE2_THRESHOLD_DAYS,
                        lead_minutes=PHASE2_LEAD_MINUTES, lead_spread=PHASE2_LEAD_SPREAD,
                        doors=PHASE2_DOCK_DOORS, door_team=PHASE2_DOOR_TEAM,
                        finite_w=PHASE2_FINITE_W,
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

    THE ANCHOR IS NOT A CROSS-PHASE VALIDITY CHECK, and saying so here was false (site-dock 06
    section 3, which rewrote this paragraph rather than amending it — a stale comment claiming a
    guarantee the campaign does not have is worse than no comment).  EVERY cell of phase 2 runs
    the COUPLED site model, `inb_off` included, because coupling is a property of the RUN and is
    DECLARED (`--couple-channels`, carried by `PHASE2_RUN_DEFAULTS`), never inferred from the
    inbound flag.  So `inb_off` is the inbound-OFF POLE INSIDE the coupled model — the control
    for "does running an inbound pipeline at all change the answer, versus which policy runs it",
    and the only cell with no yard, which makes it the structural zero for every yard quantity.

    What that trades away is the comparison with phase 1, and the trade is deliberate.  Phase 1
    is inbound-off and UNCOUPLED, so it ranks placement arms under 2x the site put labour: an
    uncoupled leaf is handed the WHOLE derived site crew, twice over (site-dock 19 measured the
    break).  Under the old leaf model `inb_off` was comparable with phase 1 but confounded
    against its own nine siblings; coupling every cell inverts that, and the matrix's own deltas
    are what the campaign publishes, so that is the side kept whole.  The asymmetry is not a
    clean scaling either — what doubles is put and receiving CAPACITY, not labour seconds, and
    under drain-or-cap a larger crew cuts less and completes more per day by an amount that
    differs per arm — so a RANK comparison across the boundary is no more invariant than an
    hours one.  The caveat is carried as a STAMP (`restock_selection.json`'s `put_regime:
    'per-leaf'`) and published with the campaign; there is no runtime gate because nothing in
    the codebase reads two run roots, so there is no join to gate.  The one reachable mistake —
    pointing the selector at a coupled root — is refused by `run_restock_selection.select`.

    The reference is `fifo`, so every delta reads "versus FIFO", which is the campaign's own
    question.  The anchor is present as a cell, never the reference.
    """
    on = dict(trailer_type='53', dock_doors=doors, door_team=door_team,
              lead_minutes=lead_minutes,
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
    # `door_team` clears here for the same reason `lead_spread` does: it is the standing
    # yard's knob and `inbound_spec()` refuses it without the flag, so an anchor inheriting
    # the cap would raise at spec build rather than run.
    axis.append(('inb_off', {**on, 'trailer_type': None, 'standing_yard': False,
                             'door_team': None,
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
        'run_defaults': ERA_RUN_DEFAULTS,
    },
    # ── the funnel, phase 2: ten inbound policies over phase 1's chosen rule PAIRS ────
    # `rule_pairs` is PHASE2_PAIRS: None until the selection artifact is read, and refused
    # rather than defaulted (see the constant).  Zoning stays off — the gain bundle refuses
    # under velocity zoning, so a zoned cell would fail at the first drain, not at spec build.
    #
    # THIS SPEC CARRIES NO `arms` KEY, DELIBERATELY.  A flat arm set beside the pairs would be
    # a second declaration of the same thing, and the one that the driver currently reads: it
    # installs a single tuple into BOTH channels, which makes the two columns the same set, and
    # `_prepare_site_run` would then zip two identical lists into a pairing that is a real
    # campaign, runs to completion, and is not the diagonal phase 1 ranked.  The per-channel
    # columns come from `channel_restocks_for` and from nowhere else, so the only reachable
    # failure is a LOUD one while the derivation is unwired.
    'inbound_policies': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['lpt'], 'rule_pairs': PHASE2_PAIRS,
        'inbound': phase2_inbound_axis(),
        'reference': 'k1_off_fifo',
        'run_defaults': PHASE2_RUN_DEFAULTS,
    },
    # ── the funnel's PILOT GATE: one throwaway cell that decides whether the campaign runs ──
    # Deliberately NOT a phase-2 cell — the arm set is not known until phase 1 ends, so reuse
    # would be circular.  Its job is to answer 10's two acceptance criteria (yard contention
    # and binding cuts) under `fifo`, calibrate `INBOUND_FEE_THRESHOLD_DAYS`, and bench the
    # window; no pilot number is ever published.
    #
    # The inbound knobs ride `run_defaults` (PILOT_RUN_DEFAULTS) rather than an inbound axis,
    # because the gate is one arrival regime, not a sweep.  The receiving crew and its day,
    # which are what actually decide whether the yard binds, are DERIVED under the era: the
    # pilot's committed regime (PHASE2_RECV_CREW_SIZE / PHASE2_RECV_DAY_SECONDS) is an error
    # there, and the gate is a one-cell inbound-on VERIFICATION read through the equilibrium
    # report, not a search ("Sequence the inbound funnel"; "Verify the derived receiving
    # crew").  40 site days, days 20-39 measured -- the reference window.
    # The scheduler is pinned to `lpt` to match phase 2: the scheduler moves batch DURATION,
    # and batch duration is what decides how many leads elapse before the next drain observes
    # them, so a pilot on the retired scheduler would calibrate a different arrival regime.
    # Two rules, both in `Inbound.gain.FAITHFUL_GAIN_FAMILIES`: `fifo` is phase 2's mandatory
    # rider and its reference, `tmin` a ranked wave, so the gate is not read off one restock
    # shape.
    'inbound_pilot': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['lpt'], 'arms': ('fifo', 'tmin'), 'reference': 'k1_off',
        'run_defaults': PILOT_RUN_DEFAULTS,
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


# ── the rule-pair shape: what a phase-2 spec is, and what it refuses ─────────────
# `PHASE2_PAIRS` is a hand-copied artifact field, and everything downstream of it is derived,
# so this is the last place a typo can be caught by anything other than a finished run.  Each
# refusal below stands between one reachable editing mistake and a ten-cell campaign that
# completes, publishes, and measures something nobody chose.

#: The two channels a coupled campaign pairs, in the declared order a pair is written in:
#: store first.  Same order `_prepare_site_run` builds its leaves in, so a rule pair's slots
#: mean the same thing in the spec, in `restock_selection.json` and in a unit's uid.
COUPLED_CHANNELS = ('store', 'fulfillment')


def _rule_pairs(pairs, where: str) -> tuple[tuple[str, str], ...]:
    """Normalise a declared rule-pair list to a tuple of `(store_rule, fulfillment_rule)`,
    raising ValueError on every shape a hand-edited spec can reach.

    `where` names the spec in the message, because the reader is looking at a registry of
    seven and needs to be told which one they broke.
    """
    if isinstance(pairs, str) or not isinstance(pairs, (list, tuple)):
        raise ValueError(
            f'{where}: `rule_pairs` must be an ordered list of (store_rule, fulfillment_rule) '
            f'pairs, got {type(pairs).__name__}. Copy `rule_pairs.chosen` out of '
            f'restock_selection.json.')
    if not pairs:
        raise ValueError(
            f'{where}: `rule_pairs` is empty. A campaign of no arm pairs is ten cells of '
            f'nothing; leave it None until phase 1 has run.')
    # THE STALE-FLAT-TUPLE CASE, named separately because it is the one an edit actually
    # reaches: `PHASE2_ARMS = ('fifo', 'tmin')` renamed but not re-shaped.  Reported as what it
    # is rather than as "entry 0 is not a pair", which reads like a typo in one entry.
    if all(isinstance(p, str) for p in pairs):
        raise ValueError(
            f'{where}: `rule_pairs` is a FLAT arm tuple {tuple(pairs)!r}, not a list of rule '
            f'pairs. `PHASE2_ARMS` became `PHASE2_PAIRS` and the shape changed with it: a '
            f'phase-2 cell pairs the two channels\' rankings rank against rank, so every entry '
            f'is (store_rule, fulfillment_rule).')
    out = []
    for i, p in enumerate(pairs):
        if isinstance(p, str) or not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError(
                f'{where}: `rule_pairs`[{i}] = {p!r} is not a (store_rule, fulfillment_rule) '
                f'pair. An arm pair has exactly two members, store first.')
        for slot, rule in zip(COUPLED_CHANNELS, p):
            if rule not in RESTOCK_KEYS:
                raise ValueError(
                    f'{where}: `rule_pairs`[{i}] names {rule!r} as its {slot} rule, which is '
                    f'not a restock rule. Known rules: {", ".join(RESTOCK_KEYS)}.')
        out.append((p[0], p[1]))
    pairs = tuple(out)

    # THE RIDER, as a PAIR.  Strictly stronger than the flat `'fifo' not in arms` it replaces:
    # `('fifo', 'rank_labor')` satisfies the old check on a set of arms and leaves the
    # fulfillment leaf with no baseline at all.
    if PHASE2_RIDER not in pairs:
        raise ValueError(
            f'{where}: `rule_pairs` has no {PHASE2_RIDER!r} rider. `fifo` is the analysis '
            f'baseline on BOTH leaves (run_channel_rollup falls back to an ARBITRARY arm '
            f'without it) and the order-blind negative control; `fifo` paired against another '
            f'rule does not give either channel one. run_restock_selection appends the rider '
            f'if phase 1 did not rank it — copy the artifact\'s list, do not retype it.')

    # NO RULE TWICE IN A COLUMN.  A rule cannot occupy two ranks, so a repeat is an editing
    # slip — and it is the slip that costs the most: `channel_restocks_for` collapses the
    # column to its distinct rules, the two columns come out RAGGED, and `_prepare_site_run`
    # refuses by length only after the parent has planned the inventory and prepared both
    # leaves.  Said here first, naming the rule and both of its ranks.
    for slot, col in zip(COUPLED_CHANNELS, zip(*pairs)):
        seen: dict[str, int] = {}
        for i, rule in enumerate(col):
            if rule in seen:
                raise ValueError(
                    f'{where}: `rule_pairs` gives the {slot} channel {rule!r} at BOTH rank '
                    f'{seen[rule]} and rank {i}. A rule occupies one rank, so a repeat '
                    f'collapses that channel\'s arm list and leaves the two channels ragged '
                    f'— which _prepare_site_run refuses only after the run has been paid for.')
            seen[rule] = i
    return pairs


def rule_pairs_of(spec) -> tuple[tuple[str, str], ...] | None:
    """The spec's declared rule pairs, normalised — or None when it declares none.

    Shape-checked on the way out, so no caller can read a half-valid pair list.
    """
    pairs = spec.get('rule_pairs')
    return None if pairs is None else _rule_pairs(pairs, '<spec>')


def channel_restocks_for(spec, channels) -> dict | None:
    """The per-channel restock subsets a spec installs into `strategies.CHANNEL_RESTOCKS`,
    keyed by channel name — or None meaning "leave it exactly as committed".

    `channels` is the run's channel names (the driver passes `CONFIG['channels']`), because
    which channels EXIST is a property of the catalogue, not of the spec.

    Four shapes, and the last is the new one:

      * `arms` absent/None  -> None.  The committed per-channel subsets stand untouched; this
        is what makes `--spec single` the old flat run.
      * `arms` = 'all'      -> {ch: None} — the full assignment-function suite everywhere.
      * `arms` = (rules…)   -> {ch: rules} — the SAME subset on every channel, which is the
        only thing a flat arm set can say.
      * `rule_pairs`        -> {'store': store column, 'fulfillment': fulfillment column},
        each in RANK ORDER, derived from the pairing and never authored beside it.

    THE COLUMNS ARE ORDERED, NOT SETS.  Site-dock 06 wrote the derivation as `{p[0] for p in
    pairs}`; a set destroys rank, `strategies_for` hands the arm list to `_prepare_site_run` in
    exactly this order, and the zip there IS the pairing — so the one thing the derivation
    exists to preserve would be dropped on the way out.  Ordered tuples, and `_rule_pairs`
    refuses the repeat that would make a column shorter than its pair list.
    """
    pairs = rule_pairs_of(spec)
    if pairs is not None:
        # A coupled campaign on a single-channel catalogue has nothing to couple.
        # `_prepare_site_run` says the same thing; it says it after the catalogue is loaded.
        if sorted(channels) != sorted(COUPLED_CHANNELS):
            raise ValueError(
                f'a rule-pair spec pairs the store and fulfillment channels, but this run has '
                f'{sorted(channels)}. Coupling is a SITE model — one dock, one receiving crew, '
                f'one pool of putters over two channels — so a single-channel catalogue has '
                f'nothing to pair. Use a mixed catalogue or a flat `arms` spec.')
        cols = dict(zip(COUPLED_CHANNELS, (tuple(c) for c in zip(*pairs))))
        return {ch: cols[ch] for ch in channels}
    arms = spec.get('arms')
    if arms is None:
        return None
    if str(arms).lower() == 'all':
        return {ch: None for ch in channels}
    return {ch: tuple(arms) for ch in channels}


def swept_rules_of(spec) -> list | None:
    """The restock rules a run SWEEPS, flat — `run_layout.json`'s `arms` field.  None means
    "the committed suite, whatever that is", which is what a spec declaring no arm set says.

    A rule-pair spec has to answer this too, and `None` would be a lie: the descriptor would
    claim the run swept the full 34-arm suite while it swept twelve rules in a diagonal.  The
    union, in first-seen order (the store column's ranks, then whatever the fulfillment column
    adds), is the honest flat answer; the PAIRING itself lives in the spec, not the descriptor.
    """
    pairs = rule_pairs_of(spec)
    if pairs is not None:
        seen: dict[str, None] = {}
        for store_rule, ful_rule in pairs:
            seen.setdefault(store_rule, None)
            seen.setdefault(ful_rule, None)
        return list(seen)
    arms = spec.get('arms')
    return None if arms in (None, 'all') else list(arms)


def validate_spec(spec, name: str = '<spec>') -> None:
    """Refuse a cell-matrix spec whose SHAPE cannot mean what it says.  Raises ValueError.

    Called from `get_spec`, which is the single door every run comes through — so this fires
    before the run directory exists, before the catalogue is read and before a worker is
    forked.  Cheap, total, and pure: it reads the spec and the rule grid, nothing else.
    """
    if spec.get('rule_pairs') is not None and spec.get('arms') is not None:
        raise ValueError(
            f'{name}: declares BOTH `arms` and `rule_pairs`. They are two answers to the same '
            f'question — which arms each channel sweeps — and a driver reading one of them '
            f'silently ignores the other. A coupled campaign states `rule_pairs` only.')

    # Through `_rule_pairs` rather than `rule_pairs_of`, so every shape message names the SPEC
    # the reader has to go and fix rather than a generic `<spec>`.
    pairs = None if spec.get('rule_pairs') is None else _rule_pairs(spec['rule_pairs'], name)
    if pairs is not None:
        # COUPLING IS DECLARED, NEVER INFERRED (site-dock 18).  Rule pairs run as ONE site;
        # uncoupled, the two columns would be swept by independent leaves and the pairing would
        # be a fiction the run tree still records arm by arm.  Nothing downstream could tell.
        if not (spec.get('run_defaults') or {}).get('couple_channels'):
            raise ValueError(
                f'{name}: declares `rule_pairs` but its `run_defaults` does not set '
                f'`couple_channels`. A rule pair is one COUPLED unit over two leaves; run '
                f'uncoupled, the two channels sweep their columns independently and the '
                f'diagonal means nothing. Carry PHASE2_RUN_DEFAULTS.')

    # AN INBOUND MATRIX MUST STATE ITS ARM SET.  Neither key means "leave CHANNEL_RESTOCKS as
    # committed", which is the full 34-arm suite — 1,360 work units where the funnel budgeted
    # 480, and not the experiment phase 2 is.  The arm set is phase 1's OUTPUT, so a matrix
    # that never received one has skipped the selection rather than chosen it.
    if spec.get('inbound') and pairs is None and spec.get('arms') is None:
        raise ValueError(
            f'{name}: an inbound cell matrix states no arm set. Phase 2 sweeps the rule PAIRS '
            f'phase 1 ranked (see run_restock_selection), not the committed default: set '
            f'whatif_config.PHASE2_PAIRS from restock_selection.json\'s `rule_pairs.chosen`, '
            f'including its mandatory {PHASE2_RIDER!r} rider.')


def get_spec(name):
    """Return the cell-matrix spec dict for a registered name (KeyError if unknown).

    Shape-checked on the way out (`validate_spec`): `run_simulation._resolve_spec` catches the
    KeyError to print the choices, so a shape refusal is raised as a ValueError and propagates.
    """
    spec = SPECS[name]
    validate_spec(spec, name)
    return spec
