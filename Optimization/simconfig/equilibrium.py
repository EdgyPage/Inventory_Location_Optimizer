"""equilibrium.py — the calibrated era's pre-registered EQUILIBRIUM CHECK, as a pure module.

"Declare the equilibrium bands" (.scratch/department-calibration, decision 4) turned "the crews
have something to do, within bounds" into one function of (db, day_lo, day_hi) returning a
verdict with reasons: four clauses over a window of working days, all STRICT.

    drained        every day in the window ended DRAINED (the persisted `shift_days` ledger) --
                   not "most": with 15% headroom one capped day in twenty means the derivation
                   is wrong, not unlucky.  A day the ledger never closed fails the same way.
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

#: The four clauses, in the order they are judged.  `released_late` is the one that raises.
CLAUSES: tuple[str, ...] = ('drained', 'released_late', 'utilization', 'missed_share')

#: Missed share may drift this much (absolute) between the window's two halves.  Decision 4;
#: an ASSUMED number, declared here rather than in settings because it is a property of the
#: check, not of any run.
MISSED_TREND_TOL: float = 0.02

#: `work_events.role` values for the two crews the check reads through `load_work_hours`.
_WORK_ROLE = {'put': 'put', 'recv': 'receive'}


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
    return {
        'pair': pair, 'channel': ch, 'day_seconds': S, 'band_tol': float(band_tol),
        'departments': departments, 'absent': absent,
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


def window_of(shift_rows) -> tuple[int, int] | None:
    """(first day, last day) the ledger closed, or None when no day was ever closed out."""
    days = [int(_get(r, 'day')) for r in shift_rows]
    return (min(days), max(days)) if days else None


def _drained_clause(shift_rows, days: list[int]) -> Clause:
    ledger = {int(_get(r, 'day')): r for r in shift_rows}
    missing = [d for d in days if d not in ledger]
    capped = [d for d in days if d in ledger and not int(_get(ledger[d], 'drained'))]
    overtime = [d for d in days if d in ledger
                and float(_get(ledger[d], 'last_finish')) > float(_get(ledger[d], 'cap_end'))]
    early = [d for d in days if d in ledger and int(_get(ledger[d], 'drained'))
             and float(_get(ledger[d], 'end_s')) < float(_get(ledger[d], 'cap_end'))]
    reading = {'days': len(days), 'drained': len(days) - len(missing) - len(capped),
               'capped': capped, 'missing': missing,
               'overtime_days': overtime, 'drained_early_days': early}
    reason = ''
    if missing:
        reason = (f'{len(missing)} day(s) in the window were never closed out by the ledger '
                  f'({missing[:6]}{" ..." if len(missing) > 6 else ""})')
    elif capped:
        reason = (f'{len(capped)} of {len(days)} day(s) ended CAPPED -- declared throughput '
                  f'not delivered ({capped[:6]}{" ..." if len(capped) > 6 else ""})')
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


def _missed_share_clause(batch_rows, days: list[int]) -> Clause:
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
        return Clause('missed_share', False,
                      {'n': n, 'level': (shares[0] if shares else None)},
                      f'only {n} batch(es) with demand in the window; a trend needs two halves')
    half = n // 2
    first = sum(shares[:half]) / half
    second = sum(shares[half:]) / (n - half)
    trend = second - first
    ok = abs(trend) <= MISSED_TREND_TOL
    reading = {'n': n, 'level': sum(shares) / n, 'first_half': first,
               'second_half': second, 'trend': trend, 'tol': MISSED_TREND_TOL}
    reason = ('' if ok else
              f'missed share trending: second half {second:.3f} vs first half {first:.3f} '
              f'({trend:+.3f}, tolerance ±{MISSED_TREND_TOL:.2f})')
    return Clause('missed_share', ok, reading, reason)


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
        'missed_share': _missed_share_clause(batch_rows, days),
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
                         f'{len(r["capped"])} capped, {len(r["missing"])} missing)')
        elif name == 'utilization':
            bits = [f'{d}={v["realized"]:.3f}/{v["expected"]:.3f}'
                    for d, v in c.reading.items() if 'realized' in v]
            parts.append(f'{name}={tag} ({", ".join(bits) or "no department expected"})')
        elif name == 'missed_share':
            r = c.reading
            lvl = r.get('level')
            parts.append(f'{name}={tag} (level {lvl:.3f}, trend {r.get("trend", float("nan")):+.3f})'
                         if isinstance(lvl, (int, float)) and not (isinstance(lvl, float) and math.isnan(lvl))
                         else f'{name}={tag} (n={r.get("n")})')
        else:
            parts.append(f'{name}={tag} (max lag {c.reading["max_lag_s"]:,.1f} s)')
    return (f'window days {verdict.day_lo}-{verdict.day_hi}: '
            f'{"PASS" if verdict.passed else "FAIL"}; ' + '; '.join(parts))
