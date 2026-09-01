"""units — the presentation-unit policy, in one place and with no third-party imports.

Every number this suite draws is in a SIM unit: durations are SECONDS, rates are items
per second, and the objective is unitless.  Turning those into something a
reader can hold is a two-part job — pick the scale, then name it — and the suite used to
do it in four places with four vocabularies (`chartkit.time_units`, two `_PRESENT` tables
and `painters.overtime_metrics`).  The names disagreed: the same quantity was
`Σ task time per batch` on one axis and `total task time per batch` on another, and both
throughput metrics printed the identical `items / hour` while measuring different things.

Two rules, and they are the whole module:

  1. A DURATION has no fixed unit.  Its readable unit depends on its own magnitude — one
     picker task is seconds, one batch of labor is hours — so it is resolved at render
     time from the pooled samples that will share the axis.  `resolve` is the only place
     that choice is made.
  2. An axis label is `stem` plus the resolved unit in parentheses, or just `stem` when
     the quantity carries no unit noun.  One composition rule, so two charts of the same
     quantity cannot disagree about what to call it.

**Deliberately stdlib-only.**  `core/quantities.py` imports this, and the point of that
module is that a test, `ingest.py` or a schema tool can read the quantity table without
dragging matplotlib and the whole analysis package into the process.  `chartkit`
re-exports `time_units` so the ~20 existing call sites keep working unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# The one declaration of what the simulator counts in.  `timeline` is stdlib-only, so
# importing it keeps this module's no-heavy-imports promise intact.
from Warehouse.kernel.timeline import SECONDS_PER_HOUR

#: (label, SECONDS per unit), largest first.  A duration renders in the largest unit
#: that keeps its typical magnitude at or above 1.
TIME_UNITS = (('hours', SECONDS_PER_HOUR), ('minutes', 60.0), ('seconds', 1.0))

#: THE seconds->days divisor, and the only one.  The yard's fee proxy compares a detention
#: span against `INBOUND_FEE_THRESHOLD_DAYS`, so somewhere a number of seconds has to become
#: a number of days — and the last time a conversion like this was spelled inline at each
#: call site, five copies of the hours divisor stayed wrong by 1000x for the life of the
#: project because each restated it rather than importing it.  It is derived from
#: `SECONDS_PER_HOUR` rather than written as 86400 for the same reason: one declaration of
#: what a sim second is, and everything downstream of it.
#:
#: A CALENDAR day, not a working day.  Detention is what the carrier's trailer is held for,
#: which accrues overnight and at weekends exactly as it does mid-shift; `WORK_DAY_SECONDS`
#: is a labour bound and would understate a standing trailer by whatever the site is closed.
SECONDS_PER_DAY = 24.0 * SECONDS_PER_HOUR

#: The fixed scale for a per-second RATE.  IMPORTED from the kernel's own declaration
#: rather than restated, so the sim's unit and the analysis layer's divisor cannot
#: disagree again — which they did, by a factor of 1000, for the life of the project:
#: this module declared milliseconds and divided by 3.6e6 while `cost_model.sec_per_inch`
#: produced seconds.  Every ABSOLUTE published figure was 1000x out; every ratio was
#: right, which is exactly why nothing caught it.
PER_HOUR = SECONDS_PER_HOUR

#: The unit kinds a quantity may declare.
#:   duration_s     sim seconds; unit resolved from the data (rule 1)
#:   rate_per_s     items per sim second; fixed scale into items/hour
#:   count          a whole-number tally in its own noun ("units", "placements")
#:   share          already a percentage or a fraction of a whole — see the note below
#:   score          a model quantity with no physical unit (Σ f·D, analytical labor W)
#:   dimensionless  a ratio or index
#:
#: `share` is load-bearing, not decorative: a percent-improvement view of a quantity that
#: is ITSELF a percentage is a percent of a percent, which no reader parses correctly.
#: `core.quantities.derive_views` reads this kind and withholds that view.
KINDS = ('duration_s', 'rate_per_s', 'count', 'share', 'score', 'dimensionless')


@dataclass(frozen=True)
class Unit:
    """How one quantity's raw values become presentation values, and what to call them.

    `suffix` is the parenthesised unit noun ('units', 'items / hour', 'W') or '' when the
    stem already reads as a complete axis label.  For `duration_s` it is ignored — the
    unit comes from the data.
    """
    kind: str
    suffix: str = ''
    scale: float = 1.0          # presentation = raw * scale (ignored for duration_ms)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f'unknown unit kind {self.kind!r} (known: {KINDS})')
        if self.kind == 'duration_s' and self.suffix:
            raise ValueError('a duration_s unit resolves its own suffix from the data; '
                             f'declaring {self.suffix!r} would be ignored, silently')


# ── the named units, so a quantity declares a unit rather than re-typing one ──────
DURATION = Unit('duration_s')
RATE_PER_HOUR = Unit('rate_per_s', 'items / hour', PER_HOUR)
NONE = Unit('dimensionless')
#: A span already expressed in DAYS — the yard's fee axis.  Deliberately NOT `duration_s`:
#: that kind resolves its own unit from the data, and a detention span of 1.7 days would
#: render in hours beside a threshold quoted in days, which is the axis-disagreement this
#: module exists to prevent.  The conversion happens once, where the frame is built, with
#: `SECONDS_PER_DAY` above; by the time a value carries this unit it is already in days,
#: so the scale is 1.  Same shape as `x_reord_vs_fifo`'s `Unit('dimensionless', 'x')`.
DAYS = Unit('dimensionless', 'days')


def _flat(values):
    """Every finite float in an arbitrarily nested iterable (numpy arrays included).

    Written against the iterator protocol rather than `np.ravel` so this module keeps its
    no-third-party promise; numpy arrays and pandas Series both iterate.
    """
    if values is None:
        return
    if isinstance(values, (str, bytes)):
        return
    try:
        it = iter(values)
    except TypeError:
        try:
            f = float(values)
        except (TypeError, ValueError):
            return
        if math.isfinite(f):
            yield f
        return
    for v in it:
        yield from _flat(v)


def time_units(values) -> tuple[float, str]:
    """(divisor, unit_label) — the largest time unit keeping typical magnitudes >= 1.

    Chosen from the pooled MEDIAN absolute magnitude so one outlier cannot drag the whole
    axis into a smaller unit.  Deterministic, so the same quantity picks the same unit on
    every re-render; the unit always appears in the axis label, so no chart can be read in
    the wrong one.  Falls back to seconds on empty/degenerate input.
    """
    vals = sorted(abs(v) for v in _flat(values) if v)
    n = len(vals)
    if n:
        typical = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    else:
        typical = 0.0
    for label, per in TIME_UNITS:
        if typical >= per:
            return per, label
    return TIME_UNITS[-1][1], TIME_UNITS[-1][0]


def resolve(unit: Unit, samples=None) -> tuple[float, str]:
    """(multiplier, unit_noun) for one unit against the samples that share its axis.

    Multiply raw values by the multiplier to get presentation values.  Pool EVERY series
    that will share the axis into one call: resolving per-series is how two boxes on one
    panel end up in different units with one label between them.
    """
    if unit.kind == 'duration_s':
        div, noun = time_units(samples if samples is not None else ())
        return 1.0 / div, noun
    return unit.scale, unit.suffix


def axis_label(stem: str, unit: Unit, samples=None) -> str:
    """The one composition rule: `stem` + the unit in parentheses, when there is one."""
    _mult, noun = resolve(unit, samples)
    return f'{stem} ({noun})' if noun else stem
