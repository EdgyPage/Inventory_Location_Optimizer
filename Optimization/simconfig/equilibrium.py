"""equilibrium.py — the calibrated era's pre-registered EQUILIBRIUM CHECK, as a pure module.

"Declare the equilibrium bands" (.scratch/department-calibration, decision 4) turned "the crews
have something to do, within bounds" into one function of (db, day_lo, day_hi) returning a
verdict with reasons: five clauses over a window of working days.  "Split the missed-share
clause into supply and labour" (2026-09-09, ADR-0004's instrument) replaced two of the
original five -- the strict `drained` and the level-free `missed_share` -- with the two
clauses the old pair was silently conflating:

    labour         the picking crew's queue is in equilibrium.  THREE terms, all judged on the
                   `carryover` FLOWS over FRESH demand (`demand_flows`, below): the realized
                   cut share (`unpicked_daycut` units over fresh units, a ratio of sums over
                   the window) sits within a band of the STAMPED expected cut share (ADR-0004,
                   `guarantee.crew.cut_share`; the band is `CUT_SHARE_Z` sampling sds of the
                   declared day law, `staffing.cut_share_sd`); the standing labour carry at
                   any day's close stays under ONE day's capacity in units (crew x S / s_pick);
                   and the per-day cut share does not trend (half-window means within
                   `CUT_SHARE_Z` sds of their difference under the same law -- the typed
                   `TREND_TOL` is half a day's spread and failed a healthy window a third of
                   the time).  A day the ledger never closed still fails it.  "Every day
                   drained" is GONE as a verdict: the day's line count is a Gaussian draw with a
                   declared cv (a third on the store), so 14 of 40 days exceed a full shift at
                   ANY headroom and a strict drained count was never a property of equilibrium
                   ("Fit the store's window to its own steady state", decision 9).  `drained`
                   survives as a READING inside this clause -- days drained, days capped, the
                   overtime and early days, the supply carry standing -- read off the ledger's
                   own verdict (`is_drained`, below, still the ONE definition the runner's
                   close-out calls).
    released_late  = 0 behind every drained day.  A self-consistency assertion, not a clause
                   that can fail: release waits on the picker clock and the cut is a
                   between-bins START gate, so a day that drained leaves exactly 0 lag on the
                   NEXT release, and lag there -- or on the first day of the run -- is an
                   INSTRUMENT BUG.  It RAISES `InstrumentError` rather than failing.
                   Lag behind a CAPPED day is that day's overrun and is recorded.
    utilization    realized utilization (worked ÷ granted, a RATIO OF SUMS over the window,
                   never a mean of per-day ratios) inside `band_tol` of the EXPECTED value the
                   derivation recorded per department per leaf -- never of ρ, which integer
                   site crews and single-channel leaves undercut by construction.  Under
                   ADR-0004 the picking expectation comes from the DERIVED crew.
    supply         the shelf's first-pass service.  The two SUPPLY reasons only
                   (`unpicked_unstocked`: no bin held the SKU; `unpicked_unavailable`: the bin
                   held less), each unit counted ONCE -- on the first batch its SKU's supply
                   failure rose -- over FRESH demand; LEVEL (a ratio of sums) within
                   `SUPPLY_LEVEL_TOL` of the record's `1 - fill_rate` (the solved floor's
                   expected first-pass missed share), TREND as before (half-window means of the
                   per-batch share within `TREND_TOL`; memories `knees-hide-from-r-squared`,
                   `per-batch-series-are-autocorrelated`).
    rework         THREE EVENTS JUDGED AT EXACTLY ZERO, the depth REPORTED PER BUCKET ("Band
                   the own-bin share and the free-index depth", 2026-09-10).  A TIER SPILL
                   (`put_spills`: the candidate chain moved up a size tier because the unit's
                   own bucket was dry), a TOP-UP into an occupied bin (`put_topups`: the whole
                   tier chain was dry), and a REPACK (the own bins were full too, judged
                   against the record's stamped `f_repack = 0`) are the same finding at three
                   rungs -- the warehouse's SIZING, never a cost to absorb into a band -- and
                   each fails the clause with the bucket(s) that read dry.  The zero for the
                   spill and the top-up is ADR-0003's own claim, hard-coded, NO KNOB
                   (decision 4): nothing on the record prices either differently from a
                   placement, so a tolerance would have no number to derive from.  The
                   free-index DEPTH is read per bucket off the `free_index` table (the leaf's
                   own section by construction; `batch_stats.free_bins` is the whole
                   geometry) against the setup `free` the record stamps per bucket, and is
                   reported -- minimum, mean and drawdown over the window -- never judged:
                   a typed level floor certifies nothing on a store whose slide outlives the
                   window (decision 6), and the trajectory band waits for the stationary
                   fragmentation closed form (34).

WHY THE SPLIT.  The old `missed_share` read `(items_demanded - total_items) / items_demanded`
per batch.  Under the era that difference is the day cut PLUS the stockout, and
`items_demanded` is the EFFECTIVE batch -- the sampled demand plus everything the previous
batch rolled forward -- so a re-offered unit was counted again in both terms (cumulative
312,302 demanded against 255,817 fresh on the store leaf of `comparison_20260908_094846`).
The clause read a labour overflow (0.037 -> 0.159 across the halves) while the supply share it
claimed to judge was flat at 0.070 against an expected 0.078.  Now every share here is a FLOW
over FRESH demand, and the two causes are two clauses with two expectations.

ONE PURE FUNCTION, and the sim never judges itself (decision 6).  It was designed with two
callers; the reference-run driver that used it as a PRECONDITION is retired ("Derive the
expected-travel closed form": there are no calibration simulations), so the callers left are
the throughput audit (`Performance_Evaluations/throughput/audit.py`), which calls it on every
run and REPORTS -- on a campaign arm a picking utilization below the band is the arm's travel
saving, the effect being measured, and a capped day is "declared throughput not delivered";
neither fails the run (decision 7) -- and `Diagnostics/equilibrium_report.py`, which prints
the same verdict per leaf off a finished run tree.

The arithmetic lives in `check_rows` over already-loaded rows so a test can prove every
clause CAN fail without a database; `check` is the thin loader in front of it.  What the
check reads, and where each number comes from:

    the ledger      `load_shift_days`   day, drained, cap_end, end_s, last_finish, and the
                                        close-out LEVELS (reported, cross-checked, never judged)
    picking         `load_batch_stats`  task_makespan (Σ task time = the crew's worked
                                        seconds), work_day, released_late, items_demanded,
                                        total_items
    the flows       `load_carryover`    (batch_id, reason, sku, qty): the pick side's four
                                        FLOWS, from which `demand_flows` recovers fresh demand
                                        and the two cause families per batch
    put / receiving `load_work_hours`   seconds per (batch, role), joined to the day
                                        through `batch_stats.work_day`
    the free index  `load_free_index`   (batch_id, handling, category, size, unit, free):
                                        the depth PER BUCKET, joined to the day through
                                        the batch; `[]` on a vintage before the table,
                                        which the rework clause reports as unrecorded

LEVELS VERSUS FLOWS.  The ledger's `standing_carry_labour` / `standing_carry_supply` are
LEVELS at close-out: what the day's LAST batch rolled forward.  They equal the day's flows only
under one batch per day (which the era completes `--releases-per-day` to), and they can never
say which units are re-attempts.  The two clauses therefore read the `carryover` flows and
report the ledger's levels beside them as a cross-check.

The GRANT is the whole declared day for every crew (crew × S per day): a day that drained
early still granted S, and utilization is against the grant, not the shift end.

No CONFIG, no settings, no run tree -- the same discipline `staffing.py` keeps.  The expected
values arrive in an `expectations` dict built by `expectations_for` from the staffing record
(the run spec's, or the copy stamped onto `sim_result` -- 03's sixth seam), so every caller
reads the same numbers through the same function.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from Optimization.simconfig.staffing import (channel_crew, cut_share as _cut_share,
                                             cut_share_sd as _cut_share_sd,
                                             picker_key)   # noqa: F401

#: The three departments the bands are drawn for, in the order the derivation records them.
DEPARTMENTS: tuple[str, ...] = ('pick', 'put', 'recv')

#: The five clauses, in the order they are judged.  `released_late` is the one that raises.
CLAUSES: tuple[str, ...] = ('labour', 'released_late', 'utilization', 'supply', 'rework')

#: The SUPPLY share may drift this much (absolute) between the window's two halves.  Decision
#: 4's missed-share tolerance; an ASSUMED number, declared here rather than in settings because
#: it is a property of the check, not of any run.  A per-batch supply share is a mean over
#: thousands of lines and moves by thousandths, so a typed band fits it.  It is NOT the labour
#: clause's trend tolerance: a per-day cut share has the day law's spread (0.06-0.12 on the
#: reference pair), and at 0.02 the labour trend failed 26-52% of healthy iid windows in a
#: Monte-Carlo (review of 2026-09-09) -- that clause derives its band, `_trend_tol` below,
#: and falls back to this number only when fewer than four days leave nothing to derive from.
TREND_TOL: float = 0.02

#: The supply LEVEL may sit this far (absolute) from the stamped `1 - fill_rate`.  ASSUMED,
#: like `TREND_TOL`: the closed form stamps the expectation but not a spread for a finite
#: window over a SKU mix (memory `window-mix-before-model-error`: a 1-2% residual on a per-unit
#: term is the window's mix before it is model error).
SUPPLY_LEVEL_TOL: float = 0.02

#: The labour LEVEL's band, in sampling standard deviations of the window's mean cut share
#: under the declared day law (`staffing.cut_share_sd / sqrt(n_days)`).  DECLARED; the sd
#: itself is derived, so the band tightens with the window and widens with the declared cv
#: rather than being a number somebody typed.
CUT_SHARE_Z: float = 2.0

#: The pick side's carryover reasons, by cause family.  SUPPLY is stock not delivered (the
#: shelf's); LABOUR is the cut's own (the crew's).  `unpicked_notasks` is a skipped batch's
#: whole demand rolling forward (neither cause) and counts toward the carry only.  All four
#: are FLOWS in pieces (`sim_semantics`); the put side's reasons are LEVELS and never read here.
SUPPLY_REASONS: tuple[str, ...] = ('unpicked_unstocked', 'unpicked_unavailable')
LABOUR_REASON: str = 'unpicked_daycut'
PICK_CARRY_REASONS: tuple[str, ...] = SUPPLY_REASONS + (LABOUR_REASON, 'unpicked_notasks')

#: `work_events.role` values for the two crews the check reads through `load_work_hours`.
_WORK_ROLE = {'put': 'put', 'recv': 'receive'}


def is_drained(*, cut: bool, standing_put: int, standing_dock: int,
               standing_carry_labour: int, overtime: bool) -> bool:
    """THE drained verdict: did working day end with its LABOUR done?

    True when nothing was cut in the day, no standing labour survives it -- no storage
    units in the put queues or held, none on the dock floor, and no demand the CUT left
    unpicked (`unpicked_daycut`, the labour carry) -- and no task finished past the cap
    (`overtime`: `last_finish > cap_end`, START-gate overtime).  Overtime is labour that
    did not fit the day, so it caps the day exactly as standing work does ("Overtime
    behind a drained day raises the instrument", 2026-09-07, amending 18): a day stamped
    drained with a finish past the whistle leaves lag on the next release, and the
    released-late clause then raises on a healthy run -- the store leaf of the line-floor
    check, day 3, 138 s of overtime with nothing standing.  The SUPPLY carry -- demand no bin could
    serve (`unpicked_unavailable`, `unpicked_unstocked`) -- is deliberately NOT an argument:
    it is stock not delivered, the `supply` clause's quantity, and a crew that realized every
    task it was given has drained its day whatever the shelf held.  Counting it made the
    verdict unreachable: under lumpy lines some SKU is always short, so 0/20 days drained on
    the reference run with pickers in band ("Choose the coverage floor", decision 7).

    The lead queue is never standing work either (transit is calendar, not labour), and
    releases are exhausted by construction at a day boundary, so neither is read.

    ONE definition, two readers: `strategy_runner._shift_close_out` writes the ledger's
    `drained` with it; the `labour` clause READS that column into its `drained` reading
    rather than re-deriving, so a pre-split vintage's verdicts stand as its runner judged
    them -- and since the split of 2026-09-09 a capped day is a READING, never a failed
    clause.  The overtime term is the one exception, and it lives in the LOADER, not the
    clause: the amendment moved no column, so no vintage separates a ledger stamped before
    it from one stamped after, and `Picking_Data.load_shift_days` serves `drained` with the
    term folded in off the row's own `last_finish` / `cap_end` -- a no-op on a ledger this
    definition wrote.
    """
    return (not cut) and (not overtime) and int(standing_put) == 0 \
        and int(standing_dock) == 0 and int(standing_carry_labour) == 0


class InstrumentError(RuntimeError):
    """The instrument contradicts itself: a nonzero `released_late` on a DRAINED day, or a
    batch whose inherited carry exceeds its stated demand.

    A drained day means nothing was cut and nothing stood at close-out, and release waits
    on the picker clock, so a batch released late into such a day is impossible unless the
    ledger or the release clamp is wrong.  `items_demanded` is the sampled demand PLUS the
    previous batch's carry, so a carry larger than the demand is impossible unless the
    runner or the rollover is wrong.  Raised, never returned as a failed clause: a failed
    clause says the SITE is out of equilibrium; this says the MEASUREMENT is.
    """


class RecordError(ValueError):
    """The staffing record does not carry what the check needs (no derived block for the
    pair, no section for the channel): the run is not an era run, or its record is broken."""


@dataclass
class Clause:
    """One clause's verdict: pass/fail, every reading it was judged on, and why."""
    name: str
    passed: bool
    reading: dict = field(default_factory=dict)
    reason: str = ''

    def as_dict(self) -> dict:
        return {'passed': bool(self.passed), 'reading': dict(self.reading),
                'reason': self.reason}


@dataclass
class Verdict:
    """The check's answer over one window: pass/fail with every clause's reading."""
    passed: bool
    day_lo: int
    day_hi: int
    clauses: dict                      # clause name -> Clause, in CLAUSES order

    @property
    def reasons(self) -> list[str]:
        return [c.reason for c in self.clauses.values() if not c.passed and c.reason]

    def as_dict(self) -> dict:
        """A JSON-ready copy: what the calibration record and the audit carry."""
        return {'passed': bool(self.passed), 'day_lo': int(self.day_lo),
                'day_hi': int(self.day_hi),
                'clauses': {k: c.as_dict() for k, c in self.clauses.items()},
                'reasons': list(self.reasons)}


# ── the expectations: what the staffing record says this leaf should show ───────────────

# `picker_key` lives with the ONE crew reader (`staffing.channel_crew`, ADR-0004; imported
# at the top) and stays importable from here for the readers that took it from this module.


def bucket_label(handling, category, size, unit) -> str:
    """The BinKey as ONE string, `handling/category/size/unit` -- the key the rework clause's
    per-bucket reading is keyed by, so `Verdict.as_dict` stays JSON-ready.  One constructor
    for the label, shared by the record side (`setup_free_by_bucket`) and the row side
    (`_rework_clause`), so the two cannot spell a bucket differently."""
    return f'{handling}/{category}/{size}/{unit}'


def setup_free_by_bucket(final_ch: dict) -> dict | None:
    """`{bucket_label: free}` off one channel's `coverage.final.<ch>` block, or None when the
    record carries no `fielded.buckets` (a run before "Field the requirement")."""
    buckets = (final_ch.get('fielded') or {}).get('buckets')
    if buckets is None:
        return None
    return {bucket_label(b['handling'], b['category'], b['size'], b['unit']): int(b['free'])
            for b in buckets}


def _expected_repacked_packs(derived: dict, inputs: dict) -> float | None:
    """The `f_repack` the record stamped, or None if it carries none.

    Prefers the DERIVED block's constant (where `derive` records it with its provenance)
    and falls back to the raw input, so a record written before the constant was recorded
    but after the input existed still answers.  Neither present means a pre-ADR-0003
    record, and the clause must report rather than judge.
    """
    recv = (derived.get('receiving') or {})
    c = recv.get('f_repack')
    if isinstance(c, dict) and c.get('value') is not None:
        return float(c['value'])
    raw = inputs.get('f_repack')
    if raw is None:
        return None
    return _packs_budget(float(raw))


def _packs_budget(f_repack: float) -> float:
    """`f_repack` (packs repacked PER PACK RECEIVED) as a pack COUNT for the window.

    At 0.0 the rate and the count coincide, which is the only value that has ever shipped.
    Any other value needs the window's received-pack count to convert, and nothing here has
    it -- so this REFUSES rather than comparing a rate to a count and silently judging a run
    against a number three orders of magnitude off.  Whoever gives `f_repack` a real value
    writes the conversion here; that is the one line that has to learn it.
    """
    if f_repack != 0.0:
        raise RecordError(
            f'f_repack = {f_repack!r}: the rework clause counts PACKS and the record holds a '
            f"per-pack RATE. The two coincide only at 0.0. Converting needs the window's "
            f'received-pack count -- write it in `_packs_budget` before declaring a nonzero '
            f'f_repack, rather than letting a rate be compared to a count.')
    return 0.0


def _guarantee_crew(section: dict, pickers: int, S: float) -> dict | None:
    """The stamped guarantee's crew block (ADR-0004) as the labour clause reads it, or None
    on a record that predates it: `{'pickers', 'load_s', 'sd_s', 'cut_share', 'cut_share_sd'}`.

    `cut_share` is the stamped expectation; `cut_share_sd` is derived from the same law
    (`staffing.cut_share_sd`) so the band around it is the law's, not a typed number.  The
    crew in the block is the solved K; `pickers` (the derived crew off `channel_crew`) is
    the same number on every record `derive` wrote, and is what the sd is priced at.
    """
    crew = ((section.get('guarantee') or {}).get('crew') or {})
    if crew.get('cut_share') is None or crew.get('load_s') is None or crew.get('sd_s') is None:
        return None
    load = float(crew['load_s']); sd = float(crew['sd_s'])
    return {'pickers': int(pickers), 'load_s': load, 'sd_s': sd,
            'cut_share': float(crew['cut_share']),
            'cut_share_sd': _cut_share_sd(load, sd, int(pickers), S)}


def expectations_for(staffing: dict, *, pair: str, channel: str | None) -> dict:
    """The expected utilization per department for ONE channel leaf, off the staffing record.

    `staffing` is the record as the run spec holds it (`{inputs, provenance, derived,
    calibration}`) -- the same object `run_analysis._sim_result_from_meta` stamps onto
    `sim_result`, so the audit passes `ctx.sim_result['staffing']` and the reference-run
    driver passes `run_spec['staffing']`.  `derived` and `calibration` are keyed BY PAIR
    because the catalogue decides the load; `pair` is the inventory label
    (`sim_result['inventory']`, `ChannelRun.pair`).  `channel` is None on a store-only run.

    Returns

        {'pair', 'channel', 'day_seconds', 'band_tol',
         'departments': {dept: {'crew': int, 'expected': float}},   # only those that apply
         'absent': {dept: why},
         'fill_rate', 'expected_missed_share',      # the supply clause's level (None pre-floor)
         'expected_cut_share', 'expected_cut_share_sd', 'guarantee',   # the labour clause's
         'expected_repacked_packs',
         'flags': {'overridden', 'saturated'}}

    A department with no crew on this site, or whose expected value the record does not
    carry for this channel, is ABSENT rather than expected at zero: a band around 0.0 would
    pass a crew that did nothing and fail one that did anything, and neither is a finding.
    Raises `RecordError` on a record with no derived block for the pair (a flag-off run) or
    no section for the channel.
    """
    ch = channel or 'store'
    inputs = staffing.get('inputs') or {}
    derived_all = staffing.get('derived') or {}
    if pair not in derived_all:
        raise RecordError(
            f'the staffing record carries no derived block for pair {pair!r} '
            f'(has: {sorted(derived_all) or "none"}); this is not a calibrated-era run')
    derived = derived_all[pair]
    section = (derived.get('channels') or {}).get(ch)
    if section is None:
        raise RecordError(f'the derived block for pair {pair!r} has no {ch!r} channel section')
    S = float(derived['day_seconds'])
    band_tol = inputs.get('band_tol')
    if band_tol is None:
        raise RecordError('the staffing inputs carry no `band_tol`')
    departments: dict = {}
    absent: dict = {}
    # The DERIVED crew first (solved under the era, ADR-0004), the declared key after.
    try:
        pickers = channel_crew(staffing, channel=ch, pair=pair)
    except KeyError:
        pickers = 0
    exp_pick = (section.get('expected_utilization') or {}).get('pick')
    s_pick = ((section.get('s_pick') or {}).get('value')) if isinstance(section.get('s_pick'), dict) else None
    if pickers > 0 and exp_pick is not None:
        departments['pick'] = {'crew': pickers, 'expected': float(exp_pick),
                               # the pair's expected seconds per unit the utilization was
                               # drawn at -- what `arm_expectations` rescales from
                               's_pick': (float(s_pick) if s_pick is not None else None)}
    else:
        absent['pick'] = 'no pickers declared for this channel'
    for dept, block_name in (('put', 'put'), ('recv', 'receiving')):
        block = derived.get(block_name) or {}
        crew = int(block.get('crew') or 0)
        exp = (block.get('expected_utilization') or {}).get(ch)
        if crew > 0 and exp is not None:
            departments[dept] = {'crew': crew, 'expected': float(exp)}
        else:
            absent[dept] = ('no site crew derived' if crew <= 0
                            else f'no expected value recorded for {ch!r}')
    cal = (staffing.get('calibration') or {}).get(pair) or {}
    # The expected first-pass fill rate the coverage loop stamped for this channel at the
    # planned levels ("Choose the coverage floor", decision 5): the `supply` clause's LEVEL is
    # read against `1 - fill_rate`.  None on a run whose record predates the line floor.
    final_ch = ((cal.get('coverage') or {}).get('final') or {}).get(ch) or {}
    fill = final_ch.get('fill') or {}
    fill_rate = fill.get('fill_rate')
    # The setup free index PER BUCKET the planner stamped for this channel's section
    # ("Field the requirement": `fielded.buckets[]` carries requirement / capacity / free per
    # BinKey).  The rework clause reads the run's `free_index` rows against it; keyed by the
    # same four coordinates joined with '/', so the reading is JSON-ready.  None on a record
    # that predates the fielded block, which the clause reports as "no setup reference".
    setup_free = setup_free_by_bucket(final_ch)
    # The stamped guarantee (ADR-0004): the `labour` clause's level is read against the
    # expected cut share of the derived crew.  None on a record that predates the guarantee
    # (the 2026-09-08 runs), which the clause reports without judging.
    guarantee = _guarantee_crew(section, pickers, S) if pickers > 0 else None
    return {
        'pair': pair, 'channel': ch, 'day_seconds': S, 'band_tol': float(band_tol),
        'departments': departments, 'absent': absent,
        'fill_rate': (float(fill_rate) if fill_rate is not None else None),
        'expected_missed_share': (1.0 - float(fill_rate) if fill_rate is not None else None),
        'expected_cut_share': (guarantee['cut_share'] if guarantee else None),
        'expected_cut_share_sd': (guarantee['cut_share_sd'] if guarantee else None),
        'guarantee': guarantee,
        # ADR-0003's rework budget, in PACKS, read off the receiving block's stamped
        # `f_repack` (0.0, provenance `assumed`).  It is a per-pack RATE in the record and a
        # count here because the clause counts packs; at 0 the two coincide, and when a real
        # coefficient replaces the 0 this is the one line that has to learn the conversion.
        # None on a record that predates the term, which the clause reports without judging
        # -- a missing expectation is not a passing one.
        'expected_repacked_packs': _expected_repacked_packs(derived, inputs),
        'setup_free': setup_free,
        'flags': {
            # A declared `--s-pick-*` / `--s-put` replaced the expectation for this pair.
            'overridden': bool(cal.get('overrides')),
            'saturated': bool((section.get('batch') or {}).get('saturated')),
        },
    }


def arm_expectations(expectations: dict, expected_pick: dict | None) -> dict:
    """The expectations for ONE ARM: the pair's, with the picking band -- and the labour
    clause's expected cut share -- re-centred on the arm's own expected seconds per unit
    under its initial placement.

    The pair's pick expectation was drawn at the class-uniform `s_pick` (the demand's
    fixed point).  An arm whose placement makes a unit cheaper is EXPECTED to run below it
    -- by the ratio of the two expectations, since the demand is shared -- and the band
    belongs around that number ("Derive the expected-travel closed form": the per-arm
    expectation is stamped for the report only).  The same ratio scales the day's load and
    its sd, so the arm's expected cut share is `cut_share(load·r, sd·r, K, S)` -- a cheaper
    unit is cut less often -- and its sampling sd follows.  Without a stamped
    `expected_pick` (a flag-off arm, an older run) the pair's expectations are returned
    unchanged; the pick department is left alone when either `s_pick` is unknown.
    """
    if not expectations or not expected_pick:
        return expectations
    pick = (expectations.get('departments') or {}).get('pick')
    s_pair = (pick or {}).get('s_pick')
    s_arm = expected_pick.get('s_pick')
    if not pick or not s_pair or not s_arm:
        return expectations
    r = float(s_arm) / float(s_pair)
    out = dict(expectations)
    out['departments'] = dict(expectations['departments'])
    out['departments']['pick'] = {**pick, 'expected': float(pick['expected']) * r,
                                  'pair_expected': float(pick['expected']),
                                  'arm_s_pick': float(s_arm)}
    g = expectations.get('guarantee')
    if g:
        S = float(expectations['day_seconds'])
        load, sd, K = g['load_s'] * r, g['sd_s'] * r, int(g['pickers'])
        out['guarantee'] = {**g, 'load_s': load, 'sd_s': sd,
                            'cut_share': _cut_share(load, sd, K, S),
                            'cut_share_sd': _cut_share_sd(load, sd, K, S),
                            'pair_cut_share': g['cut_share']}
        out['expected_cut_share'] = out['guarantee']['cut_share']
        out['expected_cut_share_sd'] = out['guarantee']['cut_share_sd']
    return out


# ── the flows: fresh demand and the two cause families, per batch ───────────────────────

def _get(row, name, default=0):
    """A field off a dataclass row or a dict row -- the loaders return both shapes."""
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _isnan(v) -> bool:
    """A pandas frame hands a NULL level back as NaN; the loaders hand it back as None."""
    return isinstance(v, float) and math.isnan(v)


def demand_flows(batch_rows, carry_rows) -> dict:
    """Per batch, the demand ledger the two flow clauses read: `{batch_id: {...}}`.

    `items_demanded` is the EFFECTIVE batch -- the sampled demand PLUS everything the
    previous batch rolled forward (`strategy_runner`: `sum(_eff_batch.items.values())`) --
    and nothing records the sampled half.  It is recovered exactly from the flows: the
    previous batch's carry is its four pick-side `carryover` reasons summed (the cut's, the
    two supply ones, and a skipped batch's `unpicked_notasks`), which is precisely
    `_pending`, so

        fresh_i = items_demanded_i - Σ carry_{i-1}

    holds whenever the carry is re-offered -- `roll_over_unpicked`, which the era completes
    to on (`run_simulation._check_era_flags`).  Flag-off the rows are recorded and NOT
    re-offered, and this identity does not hold; the check is the era's, and a caller with
    expectations has one (`expectations_for` refuses a record with no derived block).
    A carry larger than the demand is the runner contradicting itself and RAISES
    `InstrumentError`.

    Per batch:

        demanded    the effective batch (`items_demanded`)
        carry_in    the previous batch's whole pick-side carry
        fresh       demanded - carry_in
        picked      `total_items`
        cut         `unpicked_daycut` units this batch (the labour flow, re-cuts included:
                    a re-offered unit the whistle stops again is overflow again -- the
                    queue's cost, which the labour clause is there to see)
        supply      the two supply reasons this batch, re-attempts INCLUDED
        supply_new  the same, each unit counted ONCE: per SKU, the rise in the family's
                    failure count over the previous batch (`max(0, x_i - x_{i-1})`).  A LOWER
                    BOUND on the first-attempt failures: exact when the whole SKU demand fails
                    (a stockout: `x_i = fresh_i + x_{i-1}`), LOW when a restock lands
                    mid-shortage (10 carried + 6 fresh against 8 received, 8 fail -> 0 new,
                    though up to 6 fresh units failed first time), because demand has no unit
                    identity and per-SKU fresh demand is not recorded -- only the batch total
                    is.  Recording it (a schema change) is what would make this exact; until
                    then the supply level can read low against `1 - fill` when the shelf is
                    under-stocked, and a reader should weigh `units.reattempts` beside it.
                    The fill-rate expectation is a FIRST-PASS quantity, and a re-offered line
                    meets a restocked shelf -- its second failure is a second draw, not the
                    same miss twice.
        notasks     a skipped batch's demand rolling forward whole (neither family)
        recorded    False when no PICK-SIDE row exists (after the reason filter, so a table
                    holding only the put side's levels or the pre-2026-08-25 `unplaced` rows
                    does not count): the flows are unrecorded on such a vintage, and a batch
                    that served everything and a batch the table never saw both read zero
                    here -- the clauses tell them apart by whether `demanded - picked` is
                    nonzero
    """
    by_batch: dict[int, dict] = {}
    for r in carry_rows:
        reason = _get(r, 'reason', None)
        if reason not in PICK_CARRY_REASONS:
            continue                                   # the put side's LEVELS: never read
        b = by_batch.setdefault(int(_get(r, 'batch_id')), {})
        fam = b.setdefault(reason, {})
        sku = _get(r, 'sku', None)
        fam[sku] = fam.get(sku, 0) + int(_get(r, 'qty', 0) or 0)

    def _family(b: int, reasons) -> dict:
        out: dict = {}
        for reason in reasons:
            for sku, q in (by_batch.get(b) or {}).get(reason, {}).items():
                out[sku] = out.get(sku, 0) + q
        return out

    ordered = sorted(batch_rows, key=lambda r: int(_get(r, 'batch_id')))
    flows: dict = {}
    prev: int | None = None
    recorded = bool(by_batch)                          # pick-side rows, after the filter
    for r in ordered:
        b = int(_get(r, 'batch_id'))
        demanded = int(_get(r, 'items_demanded', 0) or 0)
        carry_in = sum(_family(prev, PICK_CARRY_REASONS).values()) if prev is not None else 0
        fresh = demanded - carry_in
        if fresh < 0:
            raise InstrumentError(
                f'batch {b} states {demanded:,} demanded but inherited {carry_in:,} from batch '
                f'{prev}; the carry cannot exceed the effective demand it is part of, so the '
                f'runner and the rollover disagree -- an instrument bug, not an equilibrium '
                f'failure')
        supply_now = _family(b, SUPPLY_REASONS)
        supply_prev = _family(prev, SUPPLY_REASONS) if prev is not None else {}
        supply_new = sum(max(0, q - supply_prev.get(sku, 0)) for sku, q in supply_now.items())
        flows[b] = {
            'batch_id': b, 'work_day': int(_get(r, 'work_day', 0) or 0),
            'demanded': demanded, 'carry_in': carry_in, 'fresh': fresh,
            'picked': int(_get(r, 'total_items', 0) or 0),
            'cut': sum(_family(b, (LABOUR_REASON,)).values()),
            'supply': sum(supply_now.values()), 'supply_new': supply_new,
            'notasks': sum(_family(b, ('unpicked_notasks',)).values()),
            'recorded': recorded,
        }
        prev = b
    return flows


def _unrecorded(rows) -> str:
    """The reason a flow clause cannot be judged: no carryover rows, yet demand went unserved."""
    if not rows or rows[0]['recorded']:
        return ''
    gap = sum(r['demanded'] - r['picked'] for r in rows)
    if gap <= 0:
        return ''
    return (f'no carryover rows, yet {gap:,} demanded unit(s) went unpicked in the window: the '
            f'flows are unrecorded (a pre-carryover vintage), so this clause cannot be judged')


def _halves(series: list[float]) -> tuple[float, float, float]:
    """(first-half mean, second-half mean, second - first) of an ordered series of >= 2."""
    half = len(series) // 2
    first = sum(series[:half]) / half
    second = sum(series[half:]) / (len(series) - half)
    return first, second, second - first


def _trend_tol(series: list[float], sd_day: float | None) -> tuple[float, str, float | None]:
    """The labour trend's band: `(tol, source, sd)`, `CUT_SHARE_Z` sds of the DIFFERENCE of
    two half-window means, `sd · sqrt(1/n1 + 1/n2)`.

    `sd` is the declared law's per-day spread (`expected_cut_share_sd`, source `declared`)
    when the record stamps one; without it (the 2026-09-08 runs) the window's own pooled
    WITHIN-half sample sd (source `empirical`) -- within, not overall, so a real step does not
    widen the band that judges it.  Fewer than four days leave nothing to pool, and the typed
    `TREND_TOL` stands in (source `typed`).  The typed number alone was the review's critical
    finding: half a day's spread, failing a healthy window a third of the time.
    """
    n = len(series)
    n1 = n // 2
    n2 = n - n1
    if sd_day is not None and sd_day > 0.0:
        sd = float(sd_day); source = 'declared'
    elif n >= 4:
        halves = (series[:n1], series[n1:])
        ss = 0.0
        for h in halves:
            m = sum(h) / len(h)
            ss += sum((x - m) ** 2 for x in h)
        sd = math.sqrt(ss / (n - 2)); source = 'empirical'
    else:
        return TREND_TOL, 'typed', None
    return CUT_SHARE_Z * sd * math.sqrt(1.0 / n1 + 1.0 / n2), source, sd


# ── the check over loaded rows ──────────────────────────────────────────────────────────

def window_of(shift_rows) -> tuple[int, int] | None:
    """(first day, last day) the ledger closed, or None when no day was ever closed out."""
    days = [int(_get(r, 'day')) for r in shift_rows]
    return (min(days), max(days)) if days else None


def _ledger_reading(shift_rows, days: list[int]) -> dict:
    """The ledger's per-day verdicts as a READING (written by `is_drained`), for the labour
    clause: never a verdict of its own since the split.

    `capped` are the days that ended with labour standing or a task past the cap; `missing`
    the days the ledger never closed (those DO fail the clause -- an unclosed day is not a
    measured one).  `overtime_days` are CAPPED days since the overtime amendment; through the
    loaders a drained day never appears on that list (a pre-amendment ledger is served with
    the term folded in).  `overtime_only_days` are the capped days that overtime ALONE capped
    -- nothing standing at close-out, the last task a few seconds past the whistle -- named
    apart because "declared throughput not delivered" overstates such a day: it delivered
    everything, late.  A day whose labour carry the vintage did not record (NULL) is never
    counted there.  The SUPPLY carry is reported: `supply_standing_days` closed with demand
    the shelf could not serve still rolling forward, `supply_standing_max_units` the largest
    such LEVEL (never summed across days); `supply_split_recorded` is False on the pre-split
    vintage (487a65bf83a9), whose rows carry the halves as NULL.  `labour_standing_max_units`
    is the ledger's own labour LEVEL at its worst close-out, the cross-check for the flows.
    """
    ledger = {int(_get(r, 'day')): r for r in shift_rows}
    missing = [d for d in days if d not in ledger]
    capped = [d for d in days if d in ledger and not int(_get(ledger[d], 'drained'))]
    overtime = [d for d in days if d in ledger
                and float(_get(ledger[d], 'last_finish')) > float(_get(ledger[d], 'cap_end'))]
    early = [d for d in days if d in ledger and int(_get(ledger[d], 'drained'))
             and float(_get(ledger[d], 'end_s')) < float(_get(ledger[d], 'cap_end'))]

    def _level(r, name):
        v = _get(r, name, None)
        return None if v is None or _isnan(v) else int(v)

    def _nothing_standing(r) -> bool:
        lab = _level(r, 'standing_carry_labour')
        return (int(_get(r, 'standing_put', 0) or 0) == 0
                and int(_get(r, 'standing_dock', 0) or 0) == 0
                and lab is not None and lab == 0)

    overtime_only = [d for d in overtime if d in capped and _nothing_standing(ledger[d])]
    supply = {d: _level(ledger[d], 'standing_carry_supply') for d in days if d in ledger}
    supply = {d: q for d, q in supply.items() if q is not None}
    labour = {d: _level(ledger[d], 'standing_carry_labour') for d in days if d in ledger}
    labour = {d: q for d, q in labour.items() if q is not None}
    return {'days': len(days), 'drained': len(days) - len(missing) - len(capped),
            'capped': capped, 'missing': missing,
            'overtime_days': overtime, 'overtime_only_days': overtime_only,
            'drained_early_days': early,
            'supply_standing_days': [d for d, q in supply.items() if q > 0],
            'supply_standing_max_units': max(supply.values(), default=0),
            'labour_standing_max_units': max(labour.values(), default=0),
            'supply_split_recorded': bool(supply) or not any(d in ledger for d in days)}


def _labour_clause(shift_rows, flows: dict, days: list[int], expectations: dict) -> Clause:
    """The picking queue in equilibrium: the cut share at its stamped expectation, the
    standing labour carry under a day's capacity, and the per-day cut share not trending.

    LEVEL.  `cut_share` = Σ cut ÷ Σ fresh over the window, a ratio of sums.  Judged against
    `expected_cut_share` (the guarantee's `E[(W - K·S)^+] / E[W]`, ADR-0004) within
    `CUT_SHARE_Z` sampling sds of the declared law over `n_days` days; REPORTED, not judged,
    on a record with no guarantee (the 2026-09-08 runs).  The closed form is for a day that
    starts clean, so a queue carrying overflow forward reads ABOVE it -- which is the finding.

    CARRY.  The standing labour carry at a day's close is the cut flow of the day's LAST
    batch (what rolls into the next day); it must stay under ONE day's capacity in units,
    `crew × S ÷ s_pick` (the arm's re-centred `s_pick` when stamped).  A carry past a full
    day is a queue that cannot clear, whatever the share reads.  Reported, not judged, when
    no `s_pick` is on the record to price the cap.  The ledger's own level
    (`standing_carry_labour`) rides the reading as a cross-check.

    TREND.  Half-window means of the per-day cut share within `CUT_SHARE_Z` sds of their
    difference under the day law (`_trend_tol`: the stamped sd, else the window's pooled
    within-half sd).  Under one batch per day the share is the carry over fresh demand, so a
    queue that is building or draining across the window shows here even when its mean sits
    in band.

    A day the ledger never closed fails the clause outright (an unmeasured day is not a
    drained one); a CAPPED day does not -- it is the `drained` reading's business.
    """
    ledger = _ledger_reading(shift_rows, days)
    in_window = set(days)
    rows = sorted((f for f in flows.values() if f['work_day'] in in_window),
                  key=lambda f: (f['work_day'], f['batch_id']))
    fresh = sum(f['fresh'] for f in rows)
    cut = sum(f['cut'] for f in rows)
    level = (cut / fresh) if fresh > 0 else None
    # per DAY: the share, and the carry the day's last batch rolled forward
    by_day: dict[int, dict] = {}
    for f in rows:
        d = by_day.setdefault(f['work_day'], {'fresh': 0, 'cut': 0, 'carry_end': 0})
        d['fresh'] += f['fresh']; d['cut'] += f['cut']; d['carry_end'] = f['cut']
    shares = [v['cut'] / v['fresh'] for _, v in sorted(by_day.items()) if v['fresh'] > 0]
    carry_end = {d: v['carry_end'] for d, v in by_day.items()}
    carry_max_day = max(carry_end, key=carry_end.get) if carry_end else None
    carry_max = carry_end.get(carry_max_day, 0) if carry_max_day is not None else 0

    expected = expectations.get('expected_cut_share')
    sd_day = expectations.get('expected_cut_share_sd')
    if sd_day is not None and float(sd_day) <= 0.0:
        sd_day = None                    # a spread-less law: report, never an exact-equality test
    n_days = len(shares)
    tol = (CUT_SHARE_Z * float(sd_day) / math.sqrt(n_days)
           if sd_day is not None and n_days > 0 else None)
    pick = (expectations.get('departments') or {}).get('pick') or {}
    s_pick = pick.get('arm_s_pick') or pick.get('s_pick')
    S = float(expectations['day_seconds'])
    cap_units = (int(pick['crew']) * S / float(s_pick)) if pick and s_pick else None

    reading = {
        'n': len(rows), 'n_days': n_days, 'units': {'fresh': fresh, 'cut': cut},
        'level': level, 'expected': (float(expected) if expected is not None else None),
        'delta': (level - float(expected) if expected is not None and level is not None
                  else None),
        'tol': tol, 'sd_day': (float(sd_day) if sd_day is not None else None),
        'z': CUT_SHARE_Z,
        'carry_max_units': carry_max, 'carry_max_day': carry_max_day,
        'cap_units': cap_units,
        'carry_max_days': ((carry_max / cap_units) if cap_units else None),
        'carry_end_units': carry_end,
        'drained': ledger,
    }
    reasons: list[str] = []
    unrec = _unrecorded(rows)
    if ledger['missing']:
        m = ledger['missing']
        reasons.append(f'{len(m)} day(s) in the window were never closed out by the ledger '
                       f'({m[:6]}{" ..." if len(m) > 6 else ""})')
    if unrec:
        reasons.append(unrec)
    if level is None:
        reasons.append('no fresh demand in the window; the cut share is unmeasured')
    if expected is not None and level is not None and tol is not None:
        ok = abs(level - float(expected)) <= tol
        reading['in_band'] = ok
        if not ok:
            reasons.append(f'cut share {level:.4f} vs expected {float(expected):.4f} '
                           f'({level - float(expected):+.4f}, band ±{tol:.4f} = {CUT_SHARE_Z:g} '
                           f'sd over {n_days} day(s))')
    else:
        reading['in_band'] = None                      # reported, not judged
    if cap_units is not None:
        ok = carry_max < cap_units
        reading['carry_bounded'] = ok
        if not ok:
            reasons.append(f'standing labour carry {carry_max:,} unit(s) on day {carry_max_day} '
                           f'is {carry_max / cap_units:.2f} day(s) of the crew\'s capacity '
                           f'({cap_units:,.0f} units); a queue past a full day cannot clear')
    else:
        reading['carry_bounded'] = None
    if n_days >= 2:
        first, second, trend = _halves(shares)
        t_tol, t_src, t_sd = _trend_tol(shares, sd_day)
        reading.update({'first_half': first, 'second_half': second, 'trend': trend,
                        'trend_tol': t_tol, 'trend_tol_source': t_src, 'trend_sd': t_sd})
        if abs(trend) > t_tol:
            reasons.append(f'cut share trending: second half {second:.3f} vs first half '
                           f'{first:.3f} ({trend:+.3f}, tolerance ±{t_tol:.3f} = '
                           f'{CUT_SHARE_Z:g} sd of the difference, {t_src} spread)')
    elif not reasons:
        reasons.append(f'only {n_days} day(s) with fresh demand in the window; a trend needs '
                       f'two halves')
    return Clause('labour', not reasons, reading, '; '.join(reasons))


def _released_late_clause(shift_rows, batch_rows, days: list[int]) -> Clause:
    """`released_late` = 0 on every drained day -- attributed to the day that CAUSED it.

    A batch is released late when the crew is still working the previous one, and the
    clamp erases everything but this column.  So the lag lands on the batch released INTO
    day d, but the day that overran is d - 1: a capped day 1 leaves its tail on day 2's
    release even when day 2 then drains (measured on the first era smoke run: 69.9 s on a
    drained day 2 behind a capped day 1).  The self-consistency assertion is therefore that
    lag in day d exists only behind a CAPPED day d - 1; lag behind a drained day, or on the
    first day of the run (nothing to overrun), is the instrument contradicting itself.  A
    day d - 1 the ledger never closed cannot be judged here; the labour clause reports it.
    """
    ledger = {int(_get(r, 'day')): r for r in shift_rows}
    lag_by_day: dict = {d: 0.0 for d in days}
    for b in batch_rows:
        d = int(_get(b, 'work_day'))
        if d in lag_by_day:
            lag_by_day[d] += float(_get(b, 'released_late', 0.0) or 0.0)
    behind_capped: dict = {}
    for d in days:
        lag = lag_by_day[d]
        if lag <= 0.0:
            continue
        prev = ledger.get(d - 1)
        if d == 0 or (prev is not None and int(_get(prev, 'drained'))):
            why = ('the first day of the run, which nothing can overrun' if d == 0
                   else f'day {d - 1} is recorded DRAINED')
            raise InstrumentError(
                f'the batch released into day {d} was {lag:,.1f} s late, but {why}; the '
                f'ledger and the release clamp disagree, which is an instrument bug, not '
                f'an equilibrium failure')
        if prev is not None:
            behind_capped[d] = lag
    reading = {'lag_s_total': sum(lag_by_day.values()),
               'lag_s_behind_capped_days': behind_capped,
               'max_lag_s': max(lag_by_day.values(), default=0.0)}
    return Clause('released_late', True, reading)


def _utilization_clause(batch_rows, work_rows, days: list[int], expectations: dict) -> Clause:
    S = float(expectations['day_seconds'])
    tol = float(expectations['band_tol'])
    n_days = len(days)
    in_window = set(days)
    day_of = {int(_get(b, 'batch_id')): int(_get(b, 'work_day')) for b in batch_rows}
    worked = {'pick': 0.0, 'put': 0.0, 'recv': 0.0}
    for b in batch_rows:
        if int(_get(b, 'work_day')) in in_window:
            worked['pick'] += float(_get(b, 'task_makespan', 0.0) or 0.0)
    for r in work_rows:
        d = day_of.get(int(_get(r, 'batch_id')))
        if d is None or d not in in_window:
            continue
        for dept, role in _WORK_ROLE.items():
            if _get(r, 'role') == role:
                worked[dept] += float(_get(r, 'seconds', 0.0) or 0.0)
    reading: dict = {}
    out_of_band: list[str] = []
    for dept in DEPARTMENTS:
        spec = expectations['departments'].get(dept)
        if spec is None:
            reading[dept] = {'absent': expectations['absent'].get(dept, 'not expected')}
            continue
        crew = int(spec['crew'])
        granted = crew * S * n_days
        realized = (worked[dept] / granted) if granted > 0 else 0.0
        expected = float(spec['expected'])
        delta = realized - expected
        ok = abs(delta) <= tol
        reading[dept] = {'crew': crew, 'expected': expected, 'realized': realized,
                         'delta': delta, 'worked_s': worked[dept], 'granted_s': granted,
                         'in_band': ok}
        if not ok:
            out_of_band.append(f'{dept} {realized:.3f} vs expected {expected:.3f} '
                               f'({delta:+.3f}, band ±{tol:.2f})')
    reason = ('utilization out of band: ' + '; '.join(out_of_band)) if out_of_band else ''
    return Clause('utilization', not out_of_band, reading, reason)


def _supply_clause(flows: dict, days: list[int], expected: float | None) -> Clause:
    """The shelf's first-pass service: the supply share at its stamped level, not trending.

    LEVEL.  `Σ supply_new ÷ Σ fresh` over the window -- each unit counted once, on the first
    batch its SKU's shortfall rose (`demand_flows`) -- judged within `SUPPLY_LEVEL_TOL` of
    `expected`, the record's `1 - fill_rate` (the solved floor's expected first-pass missed
    share under base stock, "Choose the coverage floor", decision 5).  A ratio of sums,
    because the fill rate is one (`Σ served ÷ Σ units`, units-weighted).  REPORTED, not
    judged, on a record with no stamped fill.

    TREND.  Half-window means of the per-batch share within `TREND_TOL`, as decision 4 had it.
    `units.supply` is the raw flow with re-attempts; `units.reattempts` is what the old
    clause was double-counting.
    """
    in_window = set(days)
    rows = sorted((f for f in flows.values() if f['work_day'] in in_window),
                  key=lambda f: (f['work_day'], f['batch_id']))
    fresh = sum(f['fresh'] for f in rows)
    new = sum(f['supply_new'] for f in rows)
    raw = sum(f['supply'] for f in rows)
    shares = [f['supply_new'] / f['fresh'] for f in rows if f['fresh'] > 0]
    level = (new / fresh) if fresh > 0 else None
    reading = {'n': len(shares), 'level': level,
               'expected': (float(expected) if expected is not None else None),
               'delta': (level - float(expected)
                         if expected is not None and level is not None else None),
               'tol': SUPPLY_LEVEL_TOL,
               'units': {'fresh': fresh, 'demanded': sum(f['demanded'] for f in rows),
                         'supply': raw, 'supply_new': new, 'reattempts': raw - new}}
    reasons: list[str] = []
    unrec = _unrecorded(rows)
    if unrec:
        reasons.append(unrec)
    if level is None:
        reasons.append('no fresh demand in the window; the supply share is unmeasured')
    if expected is not None and level is not None:
        ok = abs(level - float(expected)) <= SUPPLY_LEVEL_TOL
        reading['in_band'] = ok
        if not ok:
            reasons.append(f'supply share {level:.3f} vs expected {float(expected):.3f} off the '
                           f'stamped fill rate ({level - float(expected):+.3f}, band '
                           f'±{SUPPLY_LEVEL_TOL:.2f})')
    else:
        reading['in_band'] = None
    if len(shares) >= 2:
        first, second, trend = _halves(shares)
        reading.update({'first_half': first, 'second_half': second, 'trend': trend,
                        'trend_tol': TREND_TOL})
        if abs(trend) > TREND_TOL:
            reasons.append(f'supply share trending: second half {second:.3f} vs first half '
                           f'{first:.3f} ({trend:+.3f}, tolerance ±{TREND_TOL:.2f})')
    elif not reasons:
        reasons.append(f'only {len(shares)} batch(es) with fresh demand in the window; a trend '
                       f'needs two halves')
    return Clause('supply', not reasons, reading, '; '.join(reasons))


def _opt_int(v) -> int | None:
    """An optional count: None for a NULL the loader handed back as None or a frame handed
    back as NaN (the `free_bins` / `put_spills` rule: unknown, never zero)."""
    if v is None or _isnan(v):
        return None
    return int(v)


def _rework_clause(batch_rows, days: list[int], expected_repack_packs: float | None,
                   free_rows=None, setup_free: dict | None = None) -> Clause:
    """ADR-0003's rework, per day.  THREE EVENTS JUDGED AT ZERO; the depth REPORTED PER BUCKET.

    THE THREE EVENTS ARE ONE FINDING AT THREE RUNGS of the put-away chain, and each fails
    the clause with a SIZING message ("Band the own-bin share and the free-index depth",
    decisions 1, 2 and 4 -- ADR-0003 is the decision record; this docstring does not
    re-decide):

      * a TIER SPILL (`put_spills`): `_candidates_raw` moved up a size tier because the
        unit's own bucket had no free bin.  The FIRST deviation from the put pricer's
        per-class assumption, and the one event that fires while the other two still read 0.
      * a TOP-UP into an occupied bin (`put_topups`): the whole tier chain was dry.
      * a REPACK (`recv_repacked_packs`): the own bins were full too.  Judged against the
        record's stamped `f_repack = 0` (provenance `assumed`); `expected` None means no
        record was carried and the repack is reported without judging, because a missing
        expectation is not a passing one.

    The zero for the spill and the top-up is the ADR's own claim, hard-coded here and NOT a
    staffing key: nothing on the record prices a spill or a top-up differently from a
    placement, so a tolerance would have no number to derive from.  A run that wants a tight
    warehouse gets the finding it asked for.

    THE DEPTH IS REPORTED PER BUCKET, NEVER JUDGED (decisions 3 and 6).  `free_rows` are the
    `free_index` table's rows -- one LEVEL per BinKey per batch, the leaf's own section by
    construction -- read over the window against `setup_free`, the `free` the record stamps
    per bucket at setup (`coverage.final.<ch>.fielded.buckets`).  Per bucket: the window's
    first and last reading, minimum, mean, the drawdown (first - last), and the batches that
    read dry.  A typed level floor was rejected: it certifies nothing on a store whose slide
    outlives the window and fails a healthy run the moment the window moves; the trajectory
    band waits for the stationary fragmentation closed form (34).  When the record carries
    `setup_free`, only ITS buckets are reported -- the other section's rows are exactly the
    artefact `batch_stats.free_bins` cannot separate.  The readings this decision was drawn
    from are the corrected 2026-09-09 numbers (`comparison_20260909_204522`): 18.0% / 15.2%
    of the section free at setup (the 0.85 fill plus aisle rounding), falling 6,763 / 18,279
    bins over 40 days -- NOT the 57-59% the whole-geometry total showed.

    BATCH 0 CARRIES INITIAL STOCKING.  The runner's first snapshot folds every event since
    the manager was built into batch 0's row, so a spill or a top-up during initial
    stocking is judged like any other -- deliberately: the planner fields every bucket at
    exactly its declaration, so a spill at setup is that promise broken one bucket early,
    and on the reference pair batch 0 reads 0 / 0 (`comparison_20260910_100637`).  Batch
    0's `own_bin_share` is the one reading this skews (its denominator,
    `reorder_placements`, excludes setup), which is why the share is reported and the
    count is judged.

    VINTAGES.  `put_topups` and the two repack flows are 0 on every pre-ADR vintage BY
    CONSTRUCTION (put-away could not add to an occupied bin), so those terms pass trivially
    on an old run -- the reading's `n` says how many batches it saw.  `put_spills` and
    `free_bins` are NOT in that group: an older run spilled and had free bins and simply
    never recorded either, so both read None (unknown) rather than 0, and an unknown spill
    count cannot fail a clause judged at zero -- the reading says `spills: None` and the
    reason names it.  `free_rows` is `[]` on a vintage before the table, reported as
    "depth unrecorded per bucket", never as a warehouse with no free bins.
    """
    in_window = set(days)
    rows = [b for b in batch_rows if int(_get(b, 'work_day')) in in_window]
    day_of = {int(_get(b, 'batch_id')): int(_get(b, 'work_day')) for b in rows}
    by_day: dict[int, dict] = {}
    spills_known = False
    for b in rows:
        d = by_day.setdefault(int(_get(b, 'work_day')), {
            'topups': 0, 'spills': 0, 'places': 0, 'repacks': 0, 'packs': 0, 'free': []})
        d['topups']  += int(_get(b, 'put_topups', 0) or 0)
        d['places']  += int(_get(b, 'reorder_placements', 0) or 0)
        d['repacks'] += int(_get(b, 'recv_repacks', 0) or 0)
        d['packs']   += int(_get(b, 'recv_repacked_packs', 0) or 0)
        _sp = _opt_int(_get(b, 'put_spills', None))
        if _sp is not None:
            spills_known = True
            d['spills'] += _sp
        # UNKNOWN, not zero, on a vintage that never recorded it -- a pre-ADR run certainly
        # had free bins, and reporting a floor of 0 would read as an exhausted index, which
        # is precisely the finding this clause exists to surface.
        _free = _opt_int(_get(b, 'free_bins', None))
        if _free is not None:
            d['free'].append(_free)
    per_day = {}
    for d, v in sorted(by_day.items()):
        free = v['free']
        per_day[d] = {
            # Share of the day's placements that went into a bin already holding the SKU.
            # Against `reorder_placements` because that is what a top-up IS one of -- the
            # put-away expectation stays one placement per top-up.
            'own_bin_share': (v['topups'] / v['places']) if v['places'] else None,
            'topups': v['topups'], 'spills': (v['spills'] if spills_known else None),
            'placements': v['places'],
            'repacks': v['repacks'], 'repacked_packs': v['packs'],
            # A LEVEL sampled per batch: report the day's floor and mean, never a sum.
            'free_bins_min': min(free) if free else None,
            'free_bins_mean': (sum(free) / len(free)) if free else None,
        }
    packs  = sum(v['packs'] for v in by_day.values())
    acts   = sum(v['repacks'] for v in by_day.values())
    topups = sum(v['topups'] for v in by_day.values())
    spills = sum(v['spills'] for v in by_day.values()) if spills_known else None

    # ── the depth per bucket, over the window ────────────────────────────────────────
    # Rows are (batch, key) LEVELS; the window is the batches whose day is in it.  Kept in
    # batch order so first/last mean what they say.
    series: dict[str, list] = {}
    for r in sorted((r for r in (free_rows or []) if int(_get(r, 'batch_id')) in day_of),
                    key=lambda r: int(_get(r, 'batch_id'))):
        key = bucket_label(_get(r, 'handling'), _get(r, 'category'),
                           _get(r, 'size'), _get(r, 'unit'))
        series.setdefault(key, []).append((int(_get(r, 'batch_id')), int(_get(r, 'free'))))
    # The record's buckets when it has any (the leaf's own section); the run's own rows
    # otherwise -- an EMPTY fielded block is treated like an absent one, so the run's rows
    # still show rather than every bucket reading absent.
    keys = sorted(setup_free) if setup_free else sorted(series)
    buckets: dict[str, dict] = {}
    for key in keys:
        s = series.get(key) or []
        vals = [n for _b, n in s]
        buckets[key] = {
            'setup_free': (setup_free.get(key) if setup_free else None),
            'n': len(s),
            'first': (vals[0] if vals else None), 'last': (vals[-1] if vals else None),
            'min': (min(vals) if vals else None),
            'mean': ((sum(vals) / len(vals)) if vals else None),
            # Positive when the bucket LOST free bins over the window.
            'drawdown': ((vals[0] - vals[-1]) if vals else None),
            'dry_batches': [b for b, n in s if n == 0],
        }
    dry = [k for k, v in buckets.items() if v['dry_batches']]
    reading = {'n': len(rows), 'days': per_day,
               'topups': topups, 'spills': spills,
               'repacked_packs': packs, 'repacks': acts,
               'expected_repacked_packs': (float(expected_repack_packs)
                                           if expected_repack_packs is not None else None),
               'buckets': buckets, 'buckets_recorded': bool(series),
               'dry_buckets': dry}

    def _where() -> str:
        if not series:
            return 'per-bucket depth unrecorded on this vintage'
        if dry:
            return f'bucket(s) read dry: {", ".join(dry)}'
        return 'no bucket read dry at a batch boundary'

    reasons = []
    if spills:
        reasons.append(
            f'{spills} tier spill(s): a unit\'s own size bucket had no free bin and the chain '
            f'placed it a tier up ({_where()}); the bucket is under-sized, a finding about the '
            f'warehouse rather than a cost to absorb into a band')
    if topups:
        reasons.append(
            f'{topups} top-up(s) into an occupied bin: the whole tier chain was dry for those '
            f'units ({_where()}); a sizing finding, judged at zero with no knob')
    if expected_repack_packs is None:
        reasons.append('no f_repack in the record; the repack is reported, not judged')
        judged = not (spills or topups)
        return Clause('rework', judged, reading, '; '.join(reasons))
    if packs > float(expected_repack_packs):
        reasons.append(
            f'{packs} pack(s) repacked over {acts} rescue(s) against an expected '
            f'{float(expected_repack_packs):g}; the free index ran dry, which is a finding '
            f'about the warehouse sizing rather than a cost to absorb into a band')
    return Clause('rework', not reasons, reading, '; '.join(reasons))


def check_rows(*, shift_rows, batch_rows, work_rows, carry_rows, day_lo: int, day_hi: int,
               expectations: dict, free_rows=None) -> Verdict:
    """The five clauses over already-loaded rows.  See the module docstring for each.

    `shift_rows` are `load_shift_days` dicts; `batch_rows` are `BatchStats` (or dicts with
    the same fields); `work_rows` are `load_work_hours` dicts; `carry_rows` are
    `load_carryover` rows (dicts or dataclasses with batch_id, reason, sku, qty) -- REQUIRED,
    not defaulted, because an empty list reads as "everything served" and a caller that
    forgot to load the table must not get that answer by accident.  `free_rows` are
    `load_free_index` rows and ARE defaulted: an absent table means "the depth was never
    recorded per bucket", which the rework clause reports as exactly that -- the reading is
    never judged, so a missing list cannot manufacture a pass.  Raises `InstrumentError`
    from the released-late clause and from `demand_flows`; every other outcome is a `Verdict`.
    """
    if day_hi < day_lo:
        raise ValueError(f'empty window: day_lo={day_lo} > day_hi={day_hi}')
    days = list(range(int(day_lo), int(day_hi) + 1))
    flows = demand_flows(batch_rows, carry_rows)
    clauses = {
        'labour': _labour_clause(shift_rows, flows, days, expectations),
        'released_late': _released_late_clause(shift_rows, batch_rows, days),
        'utilization': _utilization_clause(batch_rows, work_rows, days, expectations),
        'supply': _supply_clause(flows, days, (expectations or {}).get('expected_missed_share')),
        'rework': _rework_clause(
            batch_rows, days, (expectations or {}).get('expected_repacked_packs'),
            free_rows, (expectations or {}).get('setup_free')),
    }
    return Verdict(all(c.passed for c in clauses.values()), int(day_lo), int(day_hi), clauses)


def check(db_path: str, run_id: int, day_lo: int, day_hi: int, *,
          expectations: dict) -> Verdict:
    """The check over one arm's sim DB: load the five sources, judge the window.

    `expectations` is `expectations_for(...)` for this leaf.  The loaders are the
    version-negotiating ones (`shift_days`, `work_events`, `carryover` and `free_index` are
    conditional tables: a pre-era vintage answers `[]`, which the labour clause reports as a
    window the ledger never closed rather than as a plausible pass, and the rework clause
    as a depth never recorded per bucket).
    """
    from Optimization.persistence.Picking_Data import (
        load_batch_stats, load_carryover, load_free_index, load_shift_days, load_work_hours)
    return check_rows(shift_rows=load_shift_days(db_path, run_id),
                      batch_rows=load_batch_stats(db_path, run_id),
                      work_rows=load_work_hours(db_path, run_id),
                      carry_rows=load_carryover(db_path, run_id),
                      day_lo=day_lo, day_hi=day_hi, expectations=expectations,
                      free_rows=load_free_index(db_path, run_id))


def _num(v) -> bool:
    """A number the summary can format: an int or a float that is not NaN (`_isnan`)."""
    return isinstance(v, (int, float)) and not _isnan(v)


def summarize(verdict: Verdict) -> str:
    """One log line per clause, for the equilibrium report and the audit's INFO output."""
    parts = []
    for name, c in verdict.clauses.items():
        tag = 'ok' if c.passed else 'FAIL'
        r = c.reading
        if name == 'labour':
            d = r['drained']
            lvl, exp = r.get('level'), r.get('expected')
            if _num(lvl):
                against = (f' vs expected {exp:.4f} ({r["delta"]:+.4f}, band ±{r["tol"]:.4f})'
                           if _num(exp) and _num(r.get('tol')) else '')
                carry = (f'carry max {r["carry_max_units"]:,} units'
                         + (f' = {r["carry_max_days"]:.2f} day(s)'
                            if _num(r.get('carry_max_days')) else ''))
                parts.append(
                    f'{name}={tag} (cut share {lvl:.4f}{against}; {carry}; '
                    f'trend {r.get("trend", float("nan")):+.3f}; {d["drained"]}/{d["days"]} '
                    f'drained, {len(d["capped"])} capped '
                    f'({len(d.get("overtime_only_days", ()))} by overtime alone), '
                    f'{len(d["missing"])} missing, '
                    f'{len(d.get("supply_standing_days", ()))} with supply carry standing)')
            else:
                parts.append(f'{name}={tag} (n={r.get("n")}, {len(d["missing"])} missing)')
        elif name == 'utilization':
            bits = [f'{d}={v["realized"]:.3f}/{v["expected"]:.3f}'
                    for d, v in r.items() if 'realized' in v]
            parts.append(f'{name}={tag} ({", ".join(bits) or "no department expected"})')
        elif name == 'supply':
            lvl, exp = r.get('level'), r.get('expected')
            if _num(lvl):
                against = (f', expected {exp:.3f} off the stamped fill rate ({r["delta"]:+.3f}, '
                           f'band ±{r["tol"]:.2f})' if _num(exp) else '')
                parts.append(f'{name}={tag} (level {lvl:.3f}, trend '
                             f'{r.get("trend", float("nan")):+.3f}{against}; '
                             f'{r["units"]["reattempts"]:,} re-attempt unit(s) counted once)')
            else:
                parts.append(f'{name}={tag} (n={r.get("n")})')
        elif name == 'rework':
            _shares = [v['own_bin_share'] for v in r['days'].values()
                       if v['own_bin_share'] is not None]
            _floor = [v['free_bins_min'] for v in r['days'].values()
                      if v['free_bins_min'] is not None]
            _b = r.get('buckets') or {}
            _read = {k: v for k, v in _b.items() if v.get('min') is not None}
            if _read:
                # The driest bucket by its window minimum, and the section's drawdown: the
                # two numbers a reader wants before the per-bucket table.
                _k = min(_read, key=lambda k: _read[k]['min'])
                _dd = sum(v['drawdown'] for v in _read.values())
                _depth = (f'{len(_read)} bucket(s), driest {_k} at {_read[_k]["min"]:,}'
                          + (f' of {_read[_k]["setup_free"]:,} at setup'
                             if _read[_k].get('setup_free') is not None else '')
                          + f', section drawdown {_dd:+,}')
            else:
                _depth = 'per-bucket depth unrecorded'
            _sp = r.get('spills')
            parts.append(
                f'{name}={tag} ({r.get("topups", 0)} top-up(s), '
                f'{"unrecorded" if _sp is None else _sp} spill(s), '
                f'{r["repacked_packs"]} pack(s) repacked over '
                f'{r["repacks"]} rescue(s); own-bin share '
                f'{(sum(_shares) / len(_shares)) if _shares else float("nan"):.3f}, '
                f'whole-geometry free floor {min(_floor) if _floor else "n/a"}; {_depth})')
        else:
            parts.append(f'{name}={tag} (max lag {c.reading["max_lag_s"]:,.1f} s)')
    return (f'window days {verdict.day_lo}-{verdict.day_hi}: '
            f'{"PASS" if verdict.passed else "FAIL"}; ' + '; '.join(parts))
