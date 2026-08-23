"""present — raw sim values to presentation values, for every consumer, once.

`core/quantities.py` declares what a number IS and `common/units.py` declares how a unit
is named and scaled; both are stdlib-only on purpose.  This module is the thin numpy-aware
layer between them and the painters: it hands back the `(conv, axis_label)` pair the
distribution and over-time painters have always taken, so those call sites are unchanged
while their four private lookup tables are gone.

`conv` is `None` when a quantity needs no scaling.  That is not an optimisation — it is
the contract the painters already had, and returning an identity function instead would
quietly change how `merged_effect_panel` decides whether values were converted.

The converter is a CLASS, not a closure or a lambda.  Two reasons: it is picklable, so a
spec can cross into a spawned worker; and it prints as something a reader can identify in
a traceback rather than `<lambda>`.
"""
from __future__ import annotations

import numpy as np

from Optimization.Performance_Evaluations.common import units
from Optimization.Performance_Evaluations.core import quantities as _q


class Scale:
    """Multiply raw values into presentation units.  Picklable, and named in a repr."""

    __slots__ = ('mult', 'unit_noun')

    def __init__(self, mult: float, unit_noun: str = ''):
        self.mult = float(mult)
        self.unit_noun = unit_noun

    def __call__(self, values):
        return np.asarray(values, dtype=float) * self.mult

    def __eq__(self, other):
        return (isinstance(other, Scale) and other.mult == self.mult
                and other.unit_noun == self.unit_noun)

    def __hash__(self):
        return hash((self.mult, self.unit_noun))

    def __repr__(self):
        return f'Scale(x{self.mult:g} -> {self.unit_noun or "raw"})'


def for_metric(name: str, samples=None) -> tuple:
    """(conv, axis_label) for one metric name against the samples that share its axis.

    Pool EVERY series that will land on the axis into `samples`: a duration's unit is
    resolved from its own magnitude, and resolving it per-series is how two boxes on one
    panel end up in different units with one label between them.

    `name` may be either the per-batch key or the historical aggregate spelling —
    `production_time` and `productivity_hours` are the same quantity and resolve to the
    same declaration.
    """
    q = _q.quantity_for(name)
    mult, _noun = units.resolve(q.unit, samples)
    label = q.axis_label(samples)
    return (None if mult == 1.0 else Scale(mult, _noun)), label


def pooled(values_by_key) -> list:
    """Flatten an iterable of per-key sample arrays into one list for `for_metric`.

    Exists so the callers stop each writing their own `[np.ravel(v) for v in ... if len]`
    guard; an empty pool is legal and resolves a duration to seconds.
    """
    out = []
    for v in values_by_key:
        arr = np.asarray(v, dtype=float).ravel()
        if arr.size:
            out.append(arr)
    return out
