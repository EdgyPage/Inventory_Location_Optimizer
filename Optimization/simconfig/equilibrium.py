"""equilibrium.py — the calibrated era's pre-registered EQUILIBRIUM CHECK, as a pure module.

"Declare the equilibrium bands" (.scratch/department-calibration, decision 4) turned "the crews
have something to do, within bounds" into one function of (db, day_lo, day_hi) returning a
verdict with reasons: five clauses over a window of working days, all STRICT.

    drained        every day in the window ended DRAINED (the persisted `shift_days` ledger) --
                   not "most": with 15% headroom one capped day in twenty means the derivation
                   is wrong, not unlucky.  A day the ledger never closed fails the same way.
                   DRAINED IS A LABOUR JUDGMENT (`is_drained`, below -- the ONE definition;
                   the runner's close-out calls it): nothing cut, no put or dock work
                   standing, no `unpicked_daycut` carry.  The two SUPPLY reasons the cut
                   rolls forward (`unpicked_unavailable`: the bin held less;
                   `unpicked_unstocked`: no bin held the SKU) are stock not delivered, which
                   `missed_share` already owns; counted as standing work they meant no
                   finite stock level could ever drain a day under lumpy lines ("Choose the
                   coverage floor", decision 7, amending decision 4: 0/20 days drained with
                   pickers in band and every task realized).  The supply carry stays in the
                   ledger (`standing_carry_supply`) and this clause REPORTS it per day.
                   OVERTIME CAPS A DAY ("Overtime behind a drained day raises the
                   instrument", 2026-09-07, amending 18): a day whose last task finished
                   after its cap (`last_finish > cap_end`, START-gate overtime) is labour
                   that did not fit the day, so `is_drained` takes it as its fifth term.
                   Before the amendment such a day was stamped DRAINED with its lag on
                   the next release, and the second clause raised on a healthy run (the
                   store leaf of the line-floor check, day 3: 138 s).  The amendment
                   moved no column, so no vintage records it: `load_shift_days` serves
                   the amended verdict off the row's own two stamps for every ledger, a
                   no-op on one the amended runner wrote.
    released_late  = 0 behind every drained day.  A self-consistency assertion, not a clause
                   that can fail: release waits on the picker clock and the cut is a
                   between-bins START gate, so a day that drained leaves exactly 0 lag on
                   the NEXT release, and lag there -- or on the first day of the run -- is
                   an INSTRUMENT BUG.  It RAISES `InstrumentError` rather than failing.
                   Lag behind a CAPPED day is that day's overrun and is recorded.
    utilization    realized utilization (worked ÷ granted, a RATIO OF SUMS over the window,
                   never a mean of per-day ratios) inside `band_tol` of the EXPECTED value the
                   derivation recorded per department per leaf -- never of ρ, which integer
                   site crews and single-channel leaves undercut by construction.
    missed_share   stable: the mean of the window's second half minus the mean of its first
                   half within ±0.02 absolute.  The LEVEL is recorded, never gated; no
                   regression slope (memories `knees-hide-from-r-squared`,
                   `per-batch-series-are-autocorrelated`).
    rework         no pack was REPACKED (ADR-0003).  The staffing record stamps
                   `f_repack = 0` (provenance `assumed`), so a measured repack contradicts
                   the record and fails: rework only happens once the free index is dry,
                   which is a finding about the warehouse's SIZING rather than a cost to
                   absorb into a utilization band.  The own-bin share and the free-index
                   depth ride the same reading but are REPORTED, NOT JUDGED -- they are new
                   instruments with no observed steady state, and a threshold now would be
                   invented rather than derived.

ONE PURE FUNCTION, and the sim never judges itself (decision 6).  It was designed with two
callers; the reference-run driver that used it as a PRECONDITION is retired ("Derive the
expected-travel closed form": there are no calibration simulations), so the one caller left
is the throughput audit (`Performance_Evaluations/throughput/audit.py`), which calls it on
every run and REPORTS: on a campaign arm a picking utilization below the band is the arm's travel saving --
the effect being measured -- and a capped day is "declared throughput not delivered"; neither
fails the run (decision 7).

The arithmetic lives in `check_rows` over already-loaded rows so a test can prove every
clause CAN fail without a database; `check` is the thin loader in front of it.  What the
check reads, and where each number comes from:

    the ledger      `load_shift_days`   day, drained, cap_end, end_s, last_finish
    picking         `load_batch_stats`  task_makespan (Σ task time = the crew's worked
                                        seconds), work_day, released_late, items_demanded,
                                        total_items
    put / receiving `load_work_hours`   seconds per (batch, role), joined to the day
                                        through `batch_stats.work_day`

The GRANT is the whole declared day for every crew (crew × S per day): a day that drained
early still granted S, and utilization is against the grant, not the shift end.

No CONFIG, no settings, no run tree -- the same discipline `staffing.py` keeps.  The expected
values arrive in an `expectations` dict built by `expectations_for` from the staffing record
(the run spec's, or the copy stamped onto `sim_result` -- 03's sixth seam), so both callers
read the same numbers through the same function.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

#: The three departments the bands are drawn for, in the order the derivation records them.
DEPARTMENTS: tuple[str, ...] = ('pick', 'put', 'recv')

#: The five clauses, in the order they are judged.  `released_late` is the one that raises.
CLAUSES: tuple[str, ...] = ('drained', 'released_late', 'utilization', 'missed_share',
                            'rework')

#: Missed share may drift this much (absolute) between the window's two halves.  Decision 4;
#: an ASSUMED number, declared here rather than in settings because it is a property of the
#: check, not of any run.
MISSED_TREND_TOL: float = 0.02

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
    it is stock not delivered, `missed_share`'s quantity, and a crew that realized every task
    it was given has drained its day whatever the shelf held.  Counting it made the verdict
    unreachable: under lumpy lines some SKU is always short, so 0/20 days drained on the
    reference run with pickers in band ("Choose the coverage floor", decision 7).

    The lead queue is never standing work either (transit is calendar, not labour), and
    releases are exhausted by construction at a day boundary, so neither is read.

    ONE definition, two readers: `strategy_runner._shift_close_out` writes the ledger's
    `drained` with it; `_drained_clause` reads that column rather than re-deriving, so a
    pre-split vintage's verdicts stand as its runner judged them.  The overtime term is
    the one exception, and it lives in the LOADER, not the clause: the amendment moved no
    column, so no vintage separates a ledger stamped before it from one stamped after,
    and `Picking_Data.load_shift_days` serves `drained` with the term folded in off the
    row's own `last_finish` / `cap_end` -- a no-op on a ledger this definition wrote.
    """
    return (not cut) and (not overtime) and int(standing_put) == 0 \
        and int(standing_dock) == 0 and int(standing_carry_labour) == 0


class InstrumentError(RuntimeError):
    """A nonzero `released_late` on a DRAINED day: the instrument contradicts itself.

    A drained day means nothing was cut and nothing stood at close-out, and release waits
    on the picker clock, so a batch released late into such a day is impossible unless the
    ledger or the release clamp is wrong.  Raised, never returned as a failed clause: a
    failed clause says the SITE is out of equilibrium; this says the MEASUREMENT is.
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

def picker_key(channel: str | None) -> str:
    """The staffing-inputs key holding this channel's declared picker count."""
    return 'ff_pickers' if channel == 'fulfillment' else 'store_pickers'


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
    pickers = int(inputs.get(picker_key(ch)) or section.get('pickers') or 0)
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
    # planned levels ("Choose the coverage floor", decision 5): `missed_share`'s LEVEL is read
    # against `1 - fill_rate`.  None on a run whose record predates the line floor.
    fill = (((cal.get('coverage') or {}).get('final') or {}).get(ch) or {}).get('fill') or {}
    fill_rate = fill.get('fill_rate')
    return {
        'pair': pair, 'channel': ch, 'day_seconds': S, 'band_tol': float(band_tol),
        'departments': departments, 'absent': absent,
        'fill_rate': (float(fill_rate) if fill_rate is not None else None),
        'expected_missed_share': (1.0 - float(fill_rate) if fill_rate is not None else None),
        # ADR-0003's rework budget, in PACKS, read off the receiving block's stamped
        # `f_repack` (0.0, provenance `assumed`).  It is a per-pack RATE in the record and a
        # count here because the clause counts packs; at 0 the two coincide, and when a real
        # coefficient replaces the 0 this is the one line that has to learn the conversion.
        # None on a record that predates the term, which the clause reports without judging
        # -- a missing expectation is not a passing one.
        'expected_repacked_packs': _expected_repacked_packs(derived, inputs),
        'flags': {
            # A declared `--s-pick-*` / `--s-put` replaced the expectation for this pair.
            'overridden': bool(cal.get('overrides')),
            'saturated': bool((section.get('batch') or {}).get('saturated')),
        },
    }


def arm_expectations(expectations: dict, expected_pick: dict | None) -> dict:
    """The expectations for ONE ARM: the pair's, with the picking band re-centred on the
    arm's own expected seconds per unit under its initial placement.

    The pair's pick expectation was drawn at the class-uniform `s_pick` (the demand's
    fixed point, so it equals rho by construction).  An arm whose placement makes a unit
    cheaper is EXPECTED to run below it -- by the ratio of the two expectations, since the
    demand is shared -- and the band belongs around that number ("Derive the expected-travel
    closed form": the per-arm expectation is stamped for the report only).  Without a
    stamped `expected_pick` (a flag-off arm, an older run) the pair's expectations are
    returned unchanged; the pick department is left alone when either `s_pick` is unknown.
    """
    if not expectations or not expected_pick:
        return expectations
    pick = (expectations.get('departments') or {}).get('pick')
    s_pair = (pick or {}).get('s_pick')
    s_arm = expected_pick.get('s_pick')
    if not pick or not s_pair or not s_arm:
        return expectations
    out = dict(expectations)
    out['departments'] = dict(expectations['departments'])
    out['departments']['pick'] = {**pick, 'expected': float(pick['expected']) * float(s_arm) / float(s_pair),
                                  'pair_expected': float(pick['expected']),
                                  'arm_s_pick': float(s_arm)}
    return out


# ── the check over loaded rows ──────────────────────────────────────────────────────────

def _get(row, name, default=0):
    """A field off a dataclass row or a dict row -- the loaders return both shapes."""
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _isnan(v) -> bool:
    """A pandas frame hands a NULL level back as NaN; the loaders hand it back as None."""
    return isinstance(v, float) and math.isnan(v)


def window_of(shift_rows) -> tuple[int, int] | None:
    """(first day, last day) the ledger closed, or None when no day was ever closed out."""
    days = [int(_get(r, 'day')) for r in shift_rows]
    return (min(days), max(days)) if days else None


def _drained_clause(shift_rows, days: list[int]) -> Clause:
    """Every day DRAINED, read off the ledger's own verdict (written by `is_drained`).

    The SUPPLY carry is reported beside it, never judged: `supply_standing_days` are the
    days that closed with demand the shelf could not serve still rolling forward, and
    `supply_standing_max_units` the largest such level (a LEVEL, so never summed across
    days).  A drained day on that list is the whole point of the labour-only rule -- the
    crew finished, the stock did not.  `supply_split_recorded` is False on a pre-split
    vintage (487a65bf83a9), whose rows carry the halves as NULL and whose `drained` was
    judged with the supply carry counted as standing work.  `overtime_days` are CAPPED
    days since the overtime amendment; through the loaders a drained day never appears
    on that list (a pre-amendment ledger is served with the term folded in).
    `overtime_only_days` are the capped days that overtime ALONE capped -- nothing
    standing at close-out, the last task a few seconds past the whistle -- named in the
    reason because "declared throughput not delivered" overstates such a day: it
    delivered everything, late.  A day whose labour carry the vintage did not record
    (NULL) is never counted there.
    """
    ledger = {int(_get(r, 'day')): r for r in shift_rows}
    missing = [d for d in days if d not in ledger]
    capped = [d for d in days if d in ledger and not int(_get(ledger[d], 'drained'))]
    overtime = [d for d in days if d in ledger
                and float(_get(ledger[d], 'last_finish')) > float(_get(ledger[d], 'cap_end'))]
    early = [d for d in days if d in ledger and int(_get(ledger[d], 'drained'))
             and float(_get(ledger[d], 'end_s')) < float(_get(ledger[d], 'cap_end'))]

    def _nothing_standing(r) -> bool:
        lab = _get(r, 'standing_carry_labour', None)
        return (int(_get(r, 'standing_put', 0) or 0) == 0
                and int(_get(r, 'standing_dock', 0) or 0) == 0
                and lab is not None and not _isnan(lab) and int(lab) == 0)

    overtime_only = [d for d in overtime if d in capped and _nothing_standing(ledger[d])]
    supply = {d: int(_get(ledger[d], 'standing_carry_supply', None))
              for d in days if d in ledger
              and _get(ledger[d], 'standing_carry_supply', None) is not None
              and not _isnan(_get(ledger[d], 'standing_carry_supply', None))}
    reading = {'days': len(days), 'drained': len(days) - len(missing) - len(capped),
               'capped': capped, 'missing': missing,
               'overtime_days': overtime, 'overtime_only_days': overtime_only,
               'drained_early_days': early,
               'supply_standing_days': [d for d, q in supply.items() if q > 0],
               'supply_standing_max_units': max(supply.values(), default=0),
               'supply_split_recorded': bool(supply) or not any(d in ledger for d in days)}
    reason = ''
    if missing:
        reason = (f'{len(missing)} day(s) in the window were never closed out by the ledger '
                  f'({missing[:6]}{" ..." if len(missing) > 6 else ""})')
    elif capped:
        reason = (f'{len(capped)} of {len(days)} day(s) ended CAPPED -- declared throughput '
                  f'not delivered ({capped[:6]}{" ..." if len(capped) > 6 else ""})')
        if overtime_only:
            reason += (f'; {len(overtime_only)} of them by overtime alone -- nothing standing, '
                       f'the last task finished past the cap ({overtime_only[:6]}'
                       f'{" ..." if len(overtime_only) > 6 else ""})')
    return Clause('drained', not missing and not capped, reading, reason)


def _released_late_clause(shift_rows, batch_rows, days: list[int]) -> Clause:
    """`released_late` = 0 on every drained day -- attributed to the day that CAUSED it.

    A batch is released late when the crew is still working the previous one, and the
    clamp erases everything but this column.  So the lag lands on the batch released INTO
    day d, but the day that overran is d - 1: a capped day 1 leaves its tail on day 2's
    release even when day 2 then drains (measured on the first era smoke run: 69.9 s on a
    drained day 2 behind a capped day 1).  The self-consistency assertion is therefore that
    lag in day d exists only behind a CAPPED day d - 1; lag behind a drained day, or on the
    first day of the run (nothing to overrun), is the instrument contradicting itself.  A
    day d - 1 the ledger never closed cannot be judged here; the drained clause reports it.
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


def _missed_share_clause(batch_rows, days: list[int], expected: float | None = None) -> Clause:
    """Stable, never gated on its level.  `expected` is the record's `1 - fill_rate` for this
    leaf (the expected first-pass missed share under base stock), carried into the reading as
    `expected` / `delta` so the audit can read the level against it; it moves no verdict."""
    in_window = set(days)
    rows = sorted((b for b in batch_rows if int(_get(b, 'work_day')) in in_window),
                  key=lambda b: (int(_get(b, 'work_day')), int(_get(b, 'batch_id'))))
    shares: list[float] = []
    for b in rows:
        demanded = float(_get(b, 'items_demanded', 0) or 0)
        if demanded <= 0.0:
            continue                                   # unmeasured, not perfect
        shares.append((demanded - float(_get(b, 'total_items', 0) or 0)) / demanded)
    n = len(shares)
    if n < 2:
        lvl = shares[0] if shares else None
        return Clause('missed_share', False,
                      {'n': n, 'level': lvl,
                       'expected': (float(expected) if expected is not None else None),
                       'delta': (lvl - float(expected)
                                 if expected is not None and lvl is not None else None)},
                      f'only {n} batch(es) with demand in the window; a trend needs two halves')
    half = n // 2
    first = sum(shares[:half]) / half
    second = sum(shares[half:]) / (n - half)
    trend = second - first
    ok = abs(trend) <= MISSED_TREND_TOL
    level = sum(shares) / n
    reading = {'n': n, 'level': level, 'first_half': first,
               'second_half': second, 'trend': trend, 'tol': MISSED_TREND_TOL,
               'expected': (float(expected) if expected is not None else None),
               'delta': (level - float(expected) if expected is not None else None)}
    reason = ('' if ok else
              f'missed share trending: second half {second:.3f} vs first half {first:.3f} '
              f'({trend:+.3f}, tolerance ±{MISSED_TREND_TOL:.2f})')
    return Clause('missed_share', ok, reading, reason)


def _rework_clause(batch_rows, days: list[int], expected_repack_packs: float | None) -> Clause:
    """ADR-0003's rework, per day.  JUDGED on the repack, REPORTED on the rest.

    THE ASYMMETRY IS THE DECISION.  A repack is rework the staffing record says should not
    happen at all (`f_repack = 0`, provenance `assumed`), so measuring one contradicts the
    record and the clause FAILS -- loudly, rather than being absorbed into a utilization
    band where a warehouse one size too small looks like a busy day.  `expected` is that
    stamped number; `None` means no record was carried and the clause reports without
    judging, because a missing expectation is not a passing one.

    The own-bin share and the free-index depth are the OPPOSITE case.  Both are new
    instruments with no steady state observed yet -- nobody knows what share is normal
    under base stock -- so a threshold now would be invented rather than derived.  They ride
    the reading so a run can be read, and they move no verdict until a run under ADR-0003
    shows what the steady state is.

    `put_topups` and the two repack flows are 0 on every pre-ADR vintage BY CONSTRUCTION
    (put-away could not add to an occupied bin), so this clause passes trivially on an old run
    -- the reading's `n` says how many batches it saw.  `free_bins` is NOT in that group: such
    a run had free bins and simply never recorded how many, so it reports None rather than a
    floor of 0, which would read as an exhausted index.
    """
    in_window = set(days)
    rows = [b for b in batch_rows if int(_get(b, 'work_day')) in in_window]
    by_day: dict[int, dict] = {}
    for b in rows:
        d = by_day.setdefault(int(_get(b, 'work_day')), {
            'topups': 0, 'places': 0, 'repacks': 0, 'packs': 0, 'free': []})
        d['topups']  += int(_get(b, 'put_topups', 0) or 0)
        d['places']  += int(_get(b, 'reorder_placements', 0) or 0)
        d['repacks'] += int(_get(b, 'recv_repacks', 0) or 0)
        d['packs']   += int(_get(b, 'recv_repacked_packs', 0) or 0)
        # UNKNOWN, not zero, on a vintage that never recorded it -- a pre-ADR run certainly
        # had free bins, and reporting a floor of 0 would read as an exhausted index, which
        # is precisely the finding this clause exists to surface.
        _free = _get(b, 'free_bins', None)
        if _free is not None:
            d['free'].append(int(_free))
    per_day = {}
    for d, v in sorted(by_day.items()):
        free = v['free']
        per_day[d] = {
            # Share of the day's placements that went into a bin already holding the SKU.
            # Against `reorder_placements` because that is what a top-up IS one of -- the
            # put-away expectation stays one placement per top-up.
            'own_bin_share': (v['topups'] / v['places']) if v['places'] else None,
            'topups': v['topups'], 'placements': v['places'],
            'repacks': v['repacks'], 'repacked_packs': v['packs'],
            # A LEVEL sampled per batch: report the day's floor and mean, never a sum.
            'free_bins_min': min(free) if free else None,
            'free_bins_mean': (sum(free) / len(free)) if free else None,
        }
    packs = sum(v['packs'] for v in by_day.values())
    acts  = sum(v['repacks'] for v in by_day.values())
    reading = {'n': len(rows), 'days': per_day, 'repacked_packs': packs, 'repacks': acts,
               'expected_repacked_packs': (float(expected_repack_packs)
                                           if expected_repack_packs is not None else None)}
    if expected_repack_packs is None:
        return Clause('rework', True, reading,
                      'no f_repack in the record; rework reported, not judged')
    if packs > float(expected_repack_packs):
        return Clause(
            'rework', False, reading,
            f'{packs} pack(s) repacked over {acts} rescue(s) against an expected '
            f'{float(expected_repack_packs):g}; the free index ran dry, which is a finding '
            f'about the warehouse sizing rather than a cost to absorb into a band')
    return Clause('rework', True, reading, '')


def check_rows(*, shift_rows, batch_rows, work_rows, day_lo: int, day_hi: int,
               expectations: dict) -> Verdict:
    """The four clauses over already-loaded rows.  See the module docstring for each.

    `shift_rows` are `load_shift_days` dicts; `batch_rows` are `BatchStats` (or dicts with
    the same fields); `work_rows` are `load_work_hours` dicts.  Raises `InstrumentError`
    from the second clause; every other outcome is a `Verdict`.
    """
    if day_hi < day_lo:
        raise ValueError(f'empty window: day_lo={day_lo} > day_hi={day_hi}')
    days = list(range(int(day_lo), int(day_hi) + 1))
    clauses = {
        'drained': _drained_clause(shift_rows, days),
        'released_late': _released_late_clause(shift_rows, batch_rows, days),
        'utilization': _utilization_clause(batch_rows, work_rows, days, expectations),
        'missed_share': _missed_share_clause(
            batch_rows, days, (expectations or {}).get('expected_missed_share')),
        'rework': _rework_clause(
            batch_rows, days, (expectations or {}).get('expected_repacked_packs')),
    }
    return Verdict(all(c.passed for c in clauses.values()), int(day_lo), int(day_hi), clauses)


def check(db_path: str, run_id: int, day_lo: int, day_hi: int, *,
          expectations: dict) -> Verdict:
    """The check over one arm's sim DB: load the three sources, judge the window.

    `expectations` is `expectations_for(...)` for this leaf.  The loaders are the
    version-negotiating ones (`shift_days` and `work_events` are conditional tables: a
    pre-era vintage answers `[]`, which the first clause reports as a window the ledger
    never closed rather than as a plausible pass).
    """
    from Optimization.persistence.Picking_Data import (
        load_batch_stats, load_shift_days, load_work_hours)
    return check_rows(shift_rows=load_shift_days(db_path, run_id),
                      batch_rows=load_batch_stats(db_path, run_id),
                      work_rows=load_work_hours(db_path, run_id),
                      day_lo=day_lo, day_hi=day_hi, expectations=expectations)


def summarize(verdict: Verdict) -> str:
    """One log line per clause, for the reference-run driver and the audit's INFO output."""
    parts = []
    for name, c in verdict.clauses.items():
        tag = 'ok' if c.passed else 'FAIL'
        if name == 'drained':
            r = c.reading
            parts.append(f'{name}={tag} ({r["drained"]}/{r["days"]} drained, '
                         f'{len(r["capped"])} capped '
                         f'({len(r.get("overtime_only_days", ()))} by overtime alone), '
                         f'{len(r["missing"])} missing, '
                         f'{len(r.get("supply_standing_days", ()))} with supply carry standing)')
        elif name == 'utilization':
            bits = [f'{d}={v["realized"]:.3f}/{v["expected"]:.3f}'
                    for d, v in c.reading.items() if 'realized' in v]
            parts.append(f'{name}={tag} ({", ".join(bits) or "no department expected"})')
        elif name == 'missed_share':
            r = c.reading
            lvl = r.get('level')
            exp = r.get('expected')
            against = (f', expected {exp:.3f} off the stamped fill rate ({r["delta"]:+.3f})'
                       if isinstance(exp, (int, float)) else '')
            parts.append(f'{name}={tag} (level {lvl:.3f}, trend {r.get("trend", float("nan")):+.3f}'
                         f'{against})'
                         if isinstance(lvl, (int, float)) and not (isinstance(lvl, float) and math.isnan(lvl))
                         else f'{name}={tag} (n={r.get("n")})')
        elif name == 'rework':
            r = c.reading
            _shares = [v['own_bin_share'] for v in r['days'].values()
                       if v['own_bin_share'] is not None]
            _floor = [v['free_bins_min'] for v in r['days'].values()
                      if v['free_bins_min'] is not None]
            parts.append(
                f'{name}={tag} ({r["repacked_packs"]} pack(s) repacked over '
                f'{r["repacks"]} rescue(s); own-bin share '
                f'{(sum(_shares) / len(_shares)) if _shares else float("nan"):.3f}, '
                f'free index floor {min(_floor) if _floor else "n/a"})')
        else:
            parts.append(f'{name}={tag} (max lag {c.reading["max_lag_s"]:,.1f} s)')
    return (f'window days {verdict.day_lo}-{verdict.day_hi}: '
            f'{"PASS" if verdict.passed else "FAIL"}; ' + '; '.join(parts))
