"""sim_2026_07.py — the vetted reader for the current sim-DB schema.

Covers the shape carrying `picks`, `picker_events`, `bin_placement`, `bin_eviction`,
`bin_scores`, `sku_scores`, `aisle_metrics`, `reorder_queue`, `batch_stats`, `task_stats` and
`simulation_runs`, alongside the one-table `bin_keyframe` sidecar file — and every earlier shape
still sitting in the archive, including the ones carrying `bin_inventory`.

Four ids, one reader
--------------------
`SCHEMA_IDS` is `SIM_DB_FAMILY.supported_ids()` rather than a literal tuple, so the reader
registry and the family registry cannot disagree about which files are readable — a divergence
would surface as `UnsupportedSimSchema` on a file the writer considers perfectly current.

The four shapes differ only in tables and columns THIS READER TREATS AS OPTIONAL: the
`simulation_runs.sim_schema_id` stamp column (nothing here reads it), the `bin_placement` /
`bin_eviction` log (the reader probes for it and falls back to keyframes), and `bin_inventory`
(the reverse — archive-only, probed for on the last-resort path). One implementation is correct
for all four; splitting them would duplicate ~400 lines to express "columns the reader ignores".

The PRE-stamp id is frozen as a literal on purpose.  Every run in the ~500 GB archive has that
shape, those files will never be rewritten, and the id cannot be recomputed from today's source
once the column exists.  It is not a magic number: it was derived with
`Picking_Data.observed_sim_schema_id` against the production run and matched the declared id of
the source at that commit.

Shapes older still are UNSUPPORTED, deliberately.  A viewer that guesses at columns draws a
plausible-looking warehouse that is quietly wrong; `UnsupportedSimSchema` names what differs.
"""
from __future__ import annotations

from Optimization.persistence.Picking_Data import SIM_DB_FAMILY
from Visualization.readers.base import SqliteSimReader

#: The shape every run in the archive was written with, before `sim_schema_id` existed.
#: Frozen: derivable only from the pre-column source, and those DBs are never rewritten.
PRE_STAMP_SCHEMA_ID = '23d0c7f167bc'


class Sim2026_07Reader(SqliteSimReader):
    """The current vetted reader.  All behaviour is inherited; this fixes the identity."""

    SCHEMA_IDS = SIM_DB_FAMILY.supported_ids()
