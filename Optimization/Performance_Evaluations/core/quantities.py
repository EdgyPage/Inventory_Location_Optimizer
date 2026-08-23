"""quantities — WHAT this suite measures, declared once.

`stats_core._METRICS` already proved the pattern works: it declares
`(name, source, column, direction)` and, because three consumers read it, adding a metric
adds a CSV block, a `vs_baseline` row group and an `effect_*.png` with no other edit.

It stops at presentation.  The three fields it lacks — **unit, label, stance** — were then
re-typed in four competing tables:

    significance/suite.py  `_PRESENT`            11 entries
    aggregate/sig.py       `_PRESENT`             5 entries
    common/painters.py     `overtime_metrics()`   5 entries
    headline/top_vs_baseline.py `_METRIC_GROUPS`  5 entries  (a hand-join of
                                                  `_METRICS` and `_AGG_METRICS`)

`throughput`, `makespan` and `task_mean_duration` appear in ALL FOUR with independently
maintained direction flags, and the tables had already drifted: the same quantity was
labelled `Σ task time per batch` in one and `total task time per batch` in another, while
`throughput` and `throughput_task` — different measurements — shared the single axis label
`items / hour`.  Nothing could have caught either; there was no place the two statements
met.

This module is that place.  A `Quantity` says what a number IS (unit, direction, stance),
where to read it at each scope (`Source`), what to call it, and what it may NOT be shown
as (`views_suppressed`, with a mandatory reason).  Everything downstream is derived.

## Why stdlib-only

No numpy, no matplotlib, no lambdas.  Two reasons, both load-bearing:

  * a test, `ingest.py`, or a schema tool can read this table without importing the
    analysis package — which is what makes "the emitted figure set equals the derived one"
    a checkable property rather than a convention;
  * a converter that cannot be a lambda has to be a declared `Unit`, so a conversion
    cannot be spelled inline in one consumer and forgotten in the next.  That is exactly
    how `3.6e6` came to be written out five times.

## The view derivation

`derive_views(q, shape)` replaces the per-evaluation `views=` declaration.  Coverage stops
being editorial: `layout.churn` cannot ship absolute-only beside `layout.travel`'s
delta+absolute because neither module gets a vote.  What CAN differ is the shape — a
comparison needs a baseline to compare against, and a paired delta needs per-instance
pairing — and those are properties of the mark, not opinions of the module.

Suppression is the safety valve, and its reason is mandatory (validated in
`__post_init__`, the idiom `Schema.capability.Capability` already uses for caveats).
Without it the derivation manufactures charts that are structurally valid and
meaningless — a percent-improvement of a quantity whose baseline is zero, a delta of a
share in points labelled as percent — and a meaningless chart under a caption asserting
meaning is worse than the drift it replaces.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Optimization.Performance_Evaluations.common import units
from Optimization.Performance_Evaluations.common.units import (
    DURATION, NONE, RATE_PER_HOUR, Unit)

#: The stances a quantity can be measured in.
#:
#:   level     a measurement of ONE arm.  Comparing it against the baseline arm is
#:             meaningful, so it can carry percent and delta views.
#:   contrast  ALREADY a difference between two arms.  There is no absolute of it (the
#:             number IS the comparison) and a percent of a difference restates the
#:             baseline twice, so it carries exactly the delta view.
#:
#: `cost`'s `scoring_ms_per_unit` — placement time minus the do-nothing rule's, per unit —
#: is the live example: it shipped for one day under an `absolute_` prefix, passing every
#: save-time check, before a human eye caught it.
#:
#: NOTE that `contrast` is NOT the `effect` VIEW.  An effect size with its interval is a
#: property of the significance MARK, not of the quantity, which is why it comes from the
#: shape below and never from a stance.
STANCES = ('level', 'contrast')

DIRECTIONS = ('lower', 'higher')

#: Where a quantity is read at each scope.  This dataclass IS `_METRIC_GROUPS`' hand-join:
#: the per-batch source and the steady-state field were maintained as two tables that had
#: to be zipped by eye.
#:
#: `per_batch`      (kind, column) where kind is 'batch' (a df_b column), 'task_mean'
#:                  (per-batch mean of a df_t column) or 'task_sum' (per-batch sum).
#: `steady_state`   the scalar field in a profile's series document.
#: `steady_state_name`  the name that scalar goes out under in aggregate products, when
#:                  history gave it a different one — see `production_time`.
#: `series`         (x_column, y_column, band_lo, band_hi) in the over-time series frame.
#: `runtime`        a column of the run's own COST rows — wall-clock seconds spent by the
#:                  optimiser, which is not a warehouse measurement and comes from no sim
#:                  database.
@dataclass(frozen=True)
class Source:
    per_batch: tuple | None = None
    steady_state: str | None = None
    steady_state_name: str | None = None
    series: tuple | None = None
    runtime: str | None = None

    @property
    def readable(self) -> bool:
        """True when SOMETHING can actually read this quantity.

        A declaration with no source at any scope is a declaration nothing can draw — and
        it would still satisfy the view derivation, which is why the check lives at
        construction rather than in a linter.
        """
        return bool(self.per_batch or self.steady_state or self.series or self.runtime)

    @property
    def agg_name(self) -> str | None:
        """The name this quantity carries in cross-profile products, or None if it has
        no steady-state scalar at all."""
        if self.steady_state is None:
            return None
        return self.steady_state_name


@dataclass(frozen=True)
class Quantity:
    """One measurable thing, declared once for every consumer that shows it."""
    key: str
    label: str                      # the human name; a panel title, a legend entry
    axis_stem: str                  # the axis text WITHOUT its unit — `units.axis_label`
                                    # adds the unit, so two charts cannot disagree
    unit: Unit
    direction: str
    source: Source
    stance: str = 'level'
    #: figure-filename stem for the over-time (series) family.  Seeded from today's
    #: basenames: these encode the MARK rather than the quantity (`avg_task_duration` for
    #: `task_mean_duration`, `layout_travel` for `sigma_fd`), and renaming them would
    #: break `figures.yml`, every committed `experiment.yml` and the staged images at
    #: once.  Rename deliberately, with the staging, or not at all.
    stem: str = ''
    #: over-time panel title, when this quantity has a series view
    series_title: str = ''
    #: ((view, reason), ...) — views the derivation would produce that must NOT be drawn.
    #: The reason is mandatory and is what makes this a decision rather than an omission.
    views_suppressed: tuple = ()
    notes: str = ''

    def __post_init__(self):
        if self.direction not in DIRECTIONS:
            raise ValueError(f'{self.key}: direction must be one of {DIRECTIONS}')
        if self.stance not in STANCES:
            raise ValueError(f'{self.key}: stance must be one of {STANCES}')
        if not self.source.readable:
            raise ValueError(f'{self.key} declares no source at any scope, so nothing '
                             f'can ever read it')
        for entry in self.views_suppressed:
            view, reason = entry
            if not (reason or '').strip():
                raise ValueError(
                    f'{self.key} suppresses the {view!r} view with no reason. A view '
                    f'withheld without a recorded reason is indistinguishable from one '
                    f'nobody got round to — which is the drift this module exists to end.')

    @property
    def lower_is_better(self) -> bool:
        return self.direction == 'lower'

    def axis_label(self, samples=None) -> str:
        """The axis text: stem, unit, and — for a `score` — which way is good.

        A `score` is a model quantity with no physical unit (Σ f·D, analytical labor W).
        When it also carries no unit noun, nothing on the axis tells a reader whether up
        or down is the win, so the direction is stated.  Every other kind names a unit
        that already carries the answer ('items / hour', 'units', '% of time picking'),
        and appending a hint there is noise.
        """
        label = units.axis_label(self.axis_stem, self.unit, samples)
        if self.unit.kind == 'score' and not self.unit.suffix:
            return f'{label} ({self.direction} = better)'
        return label


# ── the table ────────────────────────────────────────────────────────────────────
# ORDER IS LOAD-BEARING: `_METRICS` is derived from it verbatim and that order is the row
# order of every significance CSV.  The optimization target comes FIRST — production
# hours = Σ task time (total labor) — then the analytical objective, then the timing
# metrics, and batch-duration "makespan" last of the primaries because it is wall-time
# and therefore parallelism-dependent.

_COUNT_UNITS = Unit('count', 'units')
_LABOR_W = Unit('score', 'W')
_SCORE = Unit('score')
_SHARE = Unit('share')

QUANTITIES: tuple = (
    Quantity(
        key='production_time', label='Task makespan',
        axis_stem='Σ task time per batch', unit=DURATION, direction='lower',
        source=Source(per_batch=('task_sum', 'duration'), steady_state='ss_prod_hours',
                      steady_state_name='productivity_hours',
                      series=('task_batch', 'prod_hours', None, None)),
        stem='production_time', series_title='Production time per batch',
        notes='PRIMARY: the slotting objective, in sim units. Its cross-profile scalar '
              'has always gone out as "productivity_hours"; the two names are the same '
              'measurement and were maintained in separate tables.'),
    Quantity(
        key='objective_task_labor', label='E[task labor]',
        axis_stem='E[task labor]', unit=_LABOR_W, direction='lower',
        source=Source(per_batch=('task_mean', 'W')),
        notes='Analytical objective: E[labor of a random task] = mean task W (D+P+C). '
              'Scored from task structure, so robust to sim wall-timing noise and '
              'reorder starvation — the direct yardstick for the slotting objective. W '
              'is the cost model\'s own unit, NOT a sim-ms duration; converting it to '
              'hours would be a lie.'),
    Quantity(
        key='objective_total_labor', label='Σ task labor',
        axis_stem='Σ task labor', unit=_LABOR_W, direction='lower',
        source=Source(per_batch=('task_sum', 'W'))),
    Quantity(
        key='task_mean_duration', label='Average task duration',
        axis_stem='mean task duration', unit=DURATION, direction='lower',
        source=Source(per_batch=('task_mean', 'duration'), steady_state='ss_task_mean',
                      series=('task_batch', 'task_mean', None, None)),
        stem='avg_task_duration', series_title='Average task duration'),
    Quantity(
        key='makespan', label='Batch makespan',
        axis_stem='batch makespan', unit=DURATION, direction='lower',
        source=Source(per_batch=('batch', 'duration'), steady_state='ss_dur'),
        notes='BATCH makespan is parallel wall-clock, so it moves with crew size and '
              'scheduling as well as with placement. Secondary to production_time.'),
    Quantity(
        key='throughput', label='Thr / batch makespan',
        axis_stem='throughput / batch makespan', unit=RATE_PER_HOUR, direction='higher',
        source=Source(per_batch=('batch', 'completion_rate'), steady_state='ss_thr',
                      series=('batch', 'thr', None, None)),
        stem='throughput', series_title='Throughput'),
    Quantity(
        key='throughput_task', label='Thr / task makespan',
        axis_stem='throughput / task makespan', unit=RATE_PER_HOUR, direction='higher',
        source=Source(per_batch=('batch', 'thr_task'), steady_state='ss_thr_task')),
    Quantity(
        key='queue_depth', label='Put-away queue depth',
        axis_stem='put-away queue depth', unit=_COUNT_UNITS, direction='lower',
        source=Source(per_batch=('batch', 'queue_depth')),
        notes='The honesty metric: a placement rule that wins on picking by deferring '
              'put-away shows it here.'),
    Quantity(
        key='sigma_fd', label='Layout total f·D',
        axis_stem='total f·D', unit=_SCORE, direction='lower',
        source=Source(per_batch=('batch', 'sigma_fd'), steady_state='ss_sigma',
                      series=('batch', 'sigma_fd', None, None)),
        stem='layout_travel', series_title='Layout travel cost'),
    Quantity(
        key='picking_pct', label='% of time picking',
        axis_stem='% of time picking', unit=_SHARE, direction='higher',
        source=Source(per_batch=('batch', 'picking_pct')),
        views_suppressed=(
            ('percent', 'a percent improvement of a quantity that is itself a percentage '
                        'is a percent of a percent, which no reader parses correctly; the '
                        'honest comparison of two shares is their difference in POINTS, '
                        'which is the delta view'),)),
    Quantity(
        key='reorder_churn', label='Reorder placements per batch',
        axis_stem='reorder placements / batch', unit=Unit('count'), direction='lower',
        source=Source(per_batch=('batch', 'reorder_placements'))),

    # ── the compute-cost family: what a RULE costs to run, in wall-clock seconds ──
    # Read from the run's own cost rows rather than from a sim DB, which is what
    # `Source.runtime` names.  These measure the optimiser, not the warehouse.
    Quantity(
        key='reord_ms_per_unit', label='Placement time per unit',
        axis_stem='ms of placement per unit put away', unit=Unit('duration_ms'),
        direction='lower', source=Source(runtime='reord_ms_per_unit'),
        notes='Wall-clock milliseconds the reorder-time placement decision costs, per '
              'unit actually put away — the denominator that makes a 200-second rule '
              'comparable with a 3-second one.'),
    Quantity(
        key='scoring_ms_per_unit', label='Scoring cost over the do-nothing rule',
        axis_stem='ms of scoring per unit, over the do-nothing rule',
        unit=Unit('duration_ms'), direction='lower', stance='contrast',
        source=Source(runtime='scoring_ms_per_unit'),
        notes='(this rule - fifo) / units. A CONTRAST, not a level: it shipped for one '
              'day under an `absolute_` prefix and passed every save-time check, because '
              'each of those checks compared a declaration against another declaration '
              'and none of them ever saw a number.'),
    Quantity(
        key='x_reord_vs_fifo', label='Times the do-nothing floor',
        axis_stem='placement time as a multiple of the do-nothing rule',
        unit=Unit('dimensionless', 'x'), direction='lower',
        source=Source(runtime='x_reord_vs_fifo'),
        views_suppressed=(
            ('percent', 'this quantity is ALREADY a ratio against the baseline, so an '
                        'improvement-percent of it would be a comparison of a comparison; '
                        'the multiple is the honest unit here and the absolute view '
                        'carries it'),),
        notes='The honest unit for compute cost: absolute seconds are contended, '
              'machine-specific, and put a 10-second difference on a 210-second bar.'),

    # ── series-only quantities: no per-batch scalar, so no significance row ──────
    Quantity(
        key='pick_volume', label='Cumulative items picked',
        axis_stem='items completed', unit=Unit('count', 'items'), direction='higher',
        source=Source(series=('batch', 'cum_items', None, None)),
        stem='volume', series_title='Cumulative pick volume',
        notes='Drawn against ELAPSED HOURS rather than batch index, and cumulative, so '
              'its instances are prefixes: a per-point difference double-counts every '
              'point behind it, which is why its mark is a curve and not a serial.'),
    Quantity(
        key='task_duration', label='Task duration (median + IQR)',
        axis_stem='task duration', unit=DURATION, direction='lower',
        source=Source(series=('task_batch', 'task_median', 'task_p25', 'task_p75')),
        stem='task_duration', series_title='Task duration (median + IQR)',
        notes='The DISTRIBUTION of task duration over time, not its mean — the band is '
              'the IQR. Its scalar sibling is task_mean_duration.'),
)

BY_KEY: dict = {q.key: q for q in QUANTITIES}

#: Every name a quantity answers to, including the historical aggregate spellings, so a
#: presentation lookup keyed either way resolves to the same declaration.
BY_ANY_NAME: dict = dict(BY_KEY)
for _q in QUANTITIES:
    _agg = _q.source.agg_name
    if _agg and _agg != _q.key:
        BY_ANY_NAME[_agg] = _q

# ── the three published orders ───────────────────────────────────────────────────
# ORDER is editorial and MEMBERSHIP is not — so the orders are declared and the
# membership is derived from them, with a check that every named key can actually be read
# at that scope.  Three of them, because three different published artifacts fixed three
# different orders before this module existed and each is now committed evidence:
# reordering `AGGREGATE_ORDER` rewrites the row order of the cross-profile summary CSV,
# and reordering `HEADLINE_ORDER` renumbers the panels of the headline figure.

#: row order of the cross-profile summary CSV and the panels beside it
AGGREGATE_ORDER: tuple = ('makespan', 'throughput', 'throughput_task',
                          'task_mean_duration', 'production_time')

#: panel order of the headline top-vs-baseline figure
HEADLINE_ORDER: tuple = ('production_time', 'makespan', 'throughput',
                         'throughput_task', 'sigma_fd')

#: render order of the over-time (trajectories) family.  `pick_volume` has a series
#: source but is NOT here: it is drawn against elapsed hours by `throughput.volume`, not
#: against batch index by the shared over-time painter, so it is listed as a deliberate
#: exclusion rather than left to be noticed as an absence.
SERIES_ORDER: tuple = ('task_duration', 'task_mean_duration', 'throughput',
                       'production_time', 'sigma_fd')

#: series-sourced quantities the over-time painter does NOT draw, and why.
SERIES_ELSEWHERE: dict = {
    'pick_volume': 'drawn by throughput.volume against elapsed hours as a cumulative '
                   'curve, which is a different x axis and a different mark',
}


def _check_order(name: str, keys: tuple, attr: str) -> None:
    seen = set()
    for k in keys:
        if k in seen:
            raise AssertionError(f'{name} names {k!r} twice')
        seen.add(k)
        if getattr(BY_KEY[k].source, attr) is None:
            raise AssertionError(f'{k} is in {name} but declares no {attr} to read it '
                                 f'from, so it can never be rendered there')


_check_order('AGGREGATE_ORDER', AGGREGATE_ORDER, 'steady_state')
_check_order('HEADLINE_ORDER', HEADLINE_ORDER, 'steady_state')
_check_order('SERIES_ORDER', SERIES_ORDER, 'series')

#: every quantity with a series source must appear in SERIES_ORDER — the check that stops
#: a new over-time quantity from being declared and silently never drawn
_missing = [q.key for q in QUANTITIES
            if q.source.series is not None and q.key not in SERIES_ORDER
            and q.key not in SERIES_ELSEWHERE]
if _missing:
    raise AssertionError(f'quantities declare a series source but are absent from '
                         f'SERIES_ORDER, so nothing would render them: {_missing}')


def quantity_for(name: str) -> Quantity:
    """The declaration for a metric name, under either its per-batch or aggregate name."""
    try:
        return BY_ANY_NAME[name]
    except KeyError:
        raise KeyError(f'no quantity declares {name!r} '
                       f'(known: {sorted(BY_ANY_NAME)})') from None


# ── derived: the tables the consumers already iterate ────────────────────────────

def metric_specs() -> list:
    """`stats_core._METRICS` — (name, source_kind, column, lower_is_better)."""
    return [(q.key, q.source.per_batch[0], q.source.per_batch[1], q.lower_is_better)
            for q in QUANTITIES if q.source.per_batch is not None]


def aggregate_specs() -> list:
    """`stats_core._AGG_METRICS` — (name, steady_state_field, lower_is_better).

    Names are the aggregate spellings, which is why `production_time` goes out as
    `productivity_hours`: those strings are the `metric` column of a published CSV.
    """
    out = []
    for key in AGGREGATE_ORDER:
        q = BY_KEY[key]
        out.append((q.source.agg_name or q.key, q.source.steady_state, q.lower_is_better))
    return out


# ── derived: the view set ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Shape:
    """A mark's structural capabilities — what it CAN show, never what it should.

    `comparable`  the mark can place an arm beside a baseline arm at all.
    `paired`      the mark's instances (batches, bins, tasks) line up 1:1 between two
                  arms, so a per-instance difference is defined.
    `fixed`       for a mark whose view is a property of the mark itself rather than of
                  anything it draws — an effect panel IS the contrast, a rendered table
                  IS its cells.  A fixed shape needs no quantity to know its views.

    These live on the shape rather than the family because that is what lets `throughput`
    carry a delta (it has a per-batch series) without forcing one onto a ranked churn
    chart whose categories are arms, not paired instances.
    """
    name: str
    comparable: bool = False
    paired: bool = False
    fixed: frozenset | None = None

    @property
    def self_describing(self) -> bool:
        """True when the mark's views do not depend on what it draws."""
        return self.fixed is not None


#: One bar/dot per arm, sorted.  Comparable (the baseline is one of the categories) but
#: not paired — a category is an arm, and arms do not pair with themselves.
RANKED = Shape('ranked', comparable=True)
#: One line per arm over batches.  Batch i of one arm pairs with batch i of another.
SERIAL = Shape('serial', comparable=True, paired=True)
#: Small multiples of the serial mark — one panel per arm.  Same capabilities; the facet
#: is a LAYOUT around the mark, and a layout does not change what can be measured.
FACET = Shape('facet', comparable=True, paired=True)
#: A cumulative curve — comparable, but its instances are prefixes, so a per-point
#: difference double-counts every point behind it.
CURVE = Shape('curve', comparable=True)
#: One POINT per arm in a two-quantity plane.  Comparable against the baseline's point;
#: not paired, because a point has no instances.
SCATTER = Shape('scatter', comparable=True)
#: A stack or decomposition — travel/pick/cart inside one task.  The MEANING is the split,
#: and a split has no single value to compare, so it carries the level view alone.
COMPOSITE = Shape('composite', fixed=frozenset({'absolute'}))
#: Raw read-outs — grids, scorecards.  No baseline in the mark at all.
INSPECTION = Shape('inspection', fixed=frozenset({'absolute'}))
#: A distribution + effect-size panel: the contrast IS the mark.
EFFECT = Shape('effect', fixed=frozenset({'effect'}))
#: A rendered table: the cells are the figure.
TABLE = Shape('table', fixed=frozenset({'table'}))

SHAPES = (RANKED, SERIAL, FACET, CURVE, SCATTER, COMPOSITE, INSPECTION, EFFECT, TABLE)
SHAPE_BY_NAME: dict = {sh.name: sh for sh in SHAPES}


def derive_views(q: Quantity, shape: Shape) -> frozenset:
    """The views a quantity drawn with this shape MUST carry.

    Not "may".  This is the whole point: view coverage stops being a per-evaluation
    editorial decision and becomes a function of what the quantity is and what the mark
    can structurally show.  Two figures of the same quantity through the same shape get
    the same view set, in every family, or the derivation is wrong and gets fixed once.
    """
    if shape.self_describing:
        return shape.fixed
    if q.stance == 'contrast':
        # The number IS the comparison: there is no level of it, and a percent of a
        # difference restates the baseline twice.
        return frozenset({'delta'}) - {v for v, _r in q.views_suppressed}
    v = {'absolute'}                                   # every level has a level
    if shape.comparable:
        v.add('percent')
    if shape.paired:
        v.add('delta')
    if q.unit.kind == 'share':
        v.discard('percent')                           # a percent of a percent
    return frozenset(v) - {view for view, _reason in q.views_suppressed}


def suppression_reason(q: Quantity, view: str) -> str:
    """Why `view` is withheld for `q`, or '' if it is not withheld."""
    for v, reason in q.views_suppressed:
        if v == view:
            return reason
    return ''
