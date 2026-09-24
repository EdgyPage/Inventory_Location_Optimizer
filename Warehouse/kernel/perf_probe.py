"""perf_probe -- the inbound path's own stopwatch, read by the batch loop.

The receive, the unload plan and the put-away drain run inside ONE timed span of the batch
loop (`reord_s`), so a run could say how long the reorder phase took but never which part
of it -- the gain planner, the unload physics, the pool opens of the put drain -- cost the
time.  This module is the carve.  The code on the inbound path adds seconds and counts
here at its phase boundaries; the batch loop DRAINS it once per batch and charges the
numbers to the leaf's overlay spans (`runtime_metrics.SPANS`, the `inb_*` / `put*`
columns) and counters.

WHY A PROCESS-GLOBAL.  The measured code lives in `Inbound/` and `Warehouse/`, and the
reader in `Optimization/`; `Inbound` may not import `Optimization`, and threading a probe
object through every signature on the path would touch forty call sites to carry a
diagnostic.  The kernel is importable from every layer, and a worker process runs exactly
one work unit (recycling is pinned at 1), so one module-level accumulator per process is
one accumulator per unit.  The batch loop drains it before the first batch -- so setup
work (initial stock placed through the same pools) is never charged to the loop -- and
after every batch's receive and put drain.

IT MOVES NO NUMBER.  Every call is a dict update on a clock reading; nothing here is read
by the simulation.  An unused probe costs a few `perf_counter` calls per drain.

Spans (seconds, overlays of `reord_s`):

  inb_pre      phases 0-3: batch tick, reclaim, lead advance, fire, release
  inb_freeze   the drain's context and space-view freeze
  inb_pack     plans-at-arrival (`_plan_arrivals`)
  inb_yplan    the YARD ranking (`transit.yard_order`): the gain planner, when one is named
  inb_dplan    the DOCK ranking (`transit.dock_order`)
  inb_unload   the unload loop, net of any yard ranking done inside it
  inb_handoff  the canonical hand-off and the remainder count
  put          phase 5, the put-away drain
  put_open     pool construction inside the put drain (`_stock_ranked`)

Counters: inb_drains, yard_T_sum, yard_T_max, yard_pulls, plan_rounds, plan_places,
put_opens, put_units.
"""
from __future__ import annotations

import time

_T: dict = {}
_C: dict = {}

#: The clock every site reads, re-exported so a measured module needs one import.
now = time.perf_counter


def add(name: str, seconds: float) -> None:
    """Add `seconds` to span `name`."""
    _T[name] = _T.get(name, 0.0) + seconds


def count(name: str, n: int = 1) -> None:
    """Add `n` to counter `name`."""
    _C[name] = _C.get(name, 0) + n


def high(name: str, value: int) -> None:
    """Raise counter `name` to `value` if it is below it (a high-water mark)."""
    if value > _C.get(name, 0):
        _C[name] = value


def span(name: str) -> float:
    """The seconds span `name` holds right now (for a caller netting one span out of
    another it is timing)."""
    return _T.get(name, 0.0)


def drain() -> tuple:
    """`(spans, counters)` accumulated since the last drain, and reset both to empty."""
    t, c = dict(_T), dict(_C)
    _T.clear()
    _C.clear()
    return t, c
