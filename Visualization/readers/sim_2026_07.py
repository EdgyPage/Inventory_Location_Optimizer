"""sim_2026_07.py — the vetted reader for the current sim-DB schema.

Covers the ten-table shape carrying `picks`, `picker_events`, `bin_inventory`, `bin_scores`,
`sku_scores`, `aisle_metrics`, `reorder_queue`, `batch_stats`, `task_stats` and
`simulation_runs`, alongside the one-table `bin_keyframe` sidecar file.

Two ids, one reader
-------------------
`SCHEMA_IDS` holds both the pre- and post-stamp shapes.  They differ by exactly one nullable
column — `simulation_runs.sim_schema_id`, added so future runs record their own identity — and
nothing here reads it, so a single implementation is correct for both.  Splitting them into two
modules would duplicate ~400 lines to express "one column the reader ignores".

The PRE-stamp id is frozen as a literal on purpose.  Every run in the archive has that shape,
those files will never be rewritten (the current sweep alone is ~500 GB), and the id cannot be
recomputed from today's source once the column exists.  It is not a magic number: it was derived
with `Picking_Data.observed_sim_schema_id` against the production run and matched the declared id
of the source at that commit.

Older shapes are UNSUPPORTED, deliberately.  A viewer that guesses at columns draws a
plausible-looking warehouse that is quietly wrong; `UnsupportedSimSchema` names what differs.
"""
from __future__ import annotations

from Optimization.persistence.Picking_Data import sim_schema_id
from Visualization.readers.base import SqliteSimReader

#: The shape every run in the archive was written with, before `sim_schema_id` existed.
#: Frozen: derivable only from the pre-column source, and those DBs are never rewritten.
PRE_STAMP_SCHEMA_ID = '23d0c7f167bc'


class Sim2026_07Reader(SqliteSimReader):
    """The current vetted reader.  All behaviour is inherited; this fixes the identity."""

    SCHEMA_IDS = (PRE_STAMP_SCHEMA_ID, sim_schema_id())
