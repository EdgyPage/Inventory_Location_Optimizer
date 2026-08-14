"""protocol.py — the versioned data interface the views are written against.

`SimReader` is the ONLY surface the HTTP layer touches. When a sim DB's schema changes, a new
module in this package implements the same protocol and no view, route or front-end module
changes. That is the whole point of the split:

    view (JS)  ->  /api/<verb>  ->  route  ->  ReaderBinding  ->  SimReader  ->  sqlite

A view knows verb names and JSON. It does not know a table name, a column name, or which schema
it is reading — so redesigning a view cannot break data access, and versioning the data access
cannot break a view.

Two conventions every implementation must honour:

* **Return plain JSON-serializable data.** Dicts, lists, str/int/float/bool/None. `server.py`
  calls `jsonify` directly; there is no converter layer and there should not be one.
* **Be honest about exactness.** Any payload describing bin state carries `exact` and, when
  False, `restocks_pending` — the number of restock batches the frame is missing. See
  `RECONSTRUCTION.md` §1: a delta roll silently loses 59% of a real warehouse, and the cure is
  for the reader to SAY so rather than to draw something plausible.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


# ── exceptions ───────────────────────────────────────────────────────────────────

class SimSchemaError(Exception):
    """Base for every schema-identity failure."""


class UnsupportedSimSchema(SimSchemaError):
    """This DB's shape has no vetted reader.

    Carries a structural diff rather than a bare hash, because the realistic cause is a hybrid:
    `init_run_db` is all CREATE TABLE IF NOT EXISTS, so a DB written by old code and reopened by
    new code gains missing tables but never missing columns, matching neither declaration.
    """

    def __init__(self, observed: str, known: tuple[str, ...], detail: str = ''):
        self.observed, self.known, self.detail = observed, known, detail
        known_s = ', '.join(known) if known else '(none registered)'
        msg = (f'sim DB schema {observed} has no vetted reader. '
               f'Supported: {known_s}.')
        if detail:
            msg += f' Difference from the current declaration: {detail}'
        super().__init__(msg)


class SimSchemaDrift(SimSchemaError):
    """The stamped or pinned id disagrees with the file's actual shape."""


# ── capabilities ─────────────────────────────────────────────────────────────────
#
# Probed for ROWS, not just for columns.  `aisle_metrics` and `reorder_queue` exist in every sim
# DB but are only written by strategies that maintain that state — they are EMPTY for most arms,
# including every arm of the current production run.  A loader that returns {} for both "column
# missing" and "table empty" makes the UI render 0.0 as though it were a measurement; these flags
# are what let it hide the panel instead.

CAP_KEYFRAMES = 'keyframes'         # a .keyframes.db exists and has rows -> exact spatial state
CAP_BIN_LOG = 'bin_log'             # bin_placement rows exist -> EVERY batch is exactly rebuildable
CAP_AISLE_METRICS = 'aisle_metrics'
CAP_REORDER_QUEUE = 'reorder_queue'
CAP_BIN_SCORES = 'bin_scores'
CAP_SKU_SCORES = 'sku_scores'
CAP_VIZ_CACHE = 'viz_cache'         # a fresh sidecar is bound -> the cheap paths are available

ALL_CAPABILITIES = (CAP_KEYFRAMES, CAP_BIN_LOG, CAP_AISLE_METRICS, CAP_REORDER_QUEUE,
                    CAP_BIN_SCORES, CAP_SKU_SCORES, CAP_VIZ_CACHE)


@runtime_checkable
class SimReader(Protocol):
    """Read one arm's persisted output. One implementation per vetted schema id."""

    #: Every schema id this implementation is vetted for. A tuple because one reader
    #: legitimately spans shapes whose only delta is a column it already tolerates.
    SCHEMA_IDS: tuple[str, ...]

    # ── identity ──
    def schema_id(self) -> str:
        """The resolved schema id of the DB behind this reader."""

    def capabilities(self) -> frozenset[str]:
        """Which optional data this arm actually HAS. Probed for rows, cached."""

    # ── static, per run ──
    def run_meta(self) -> dict:
        """Run params + identity + the batch/keyframe grid the UI scrubs over."""

    def aisle_geometry(self) -> list[dict]:
        """One row per aisle: id, family, bay_x, bay_y, capacity. NO per-bin list.

        The bin grid is generated from bay_x x bay_y on demand — materializing it for all
        aisles is ~396,500 dicts and a ~40 MB response on a production warehouse.
        """

    def aisle_bins(self, aisle: int) -> list[dict]:
        """Every bin position in one aisle, including the empty ones."""

    def batch_index(self) -> list[dict]:
        """Per batch: id, duration, items, whether it is a keyframe batch.

        Built from `batch_stats`, never `range(n_batches)` — a batch that produced no tasks
        writes no row and the list legitimately has holes.
        """

    def bin_scores(self, aisles: list[int] | None = None) -> dict:
        """Static per-bin layout cost, optionally scoped to some aisles."""

    def sku_scores(self, skus: list[int] | None = None) -> dict:
        """Static per-SKU placement scores, optionally scoped."""

    # ── state ──
    def state_at(self, batch: int, aisles: list[int] | None = None,
                 t: float | None = None) -> dict:
        """Occupied bins at (batch, t). Carries `exact` and `restocks_pending`."""

    def aisle_state(self, batch: int, aisle: int, t: float | None = None) -> dict:
        """`state_at` for one aisle, plus that aisle's geometry and scores."""

    def bin_history(self, aisle: int, bayX: int, bayY: int) -> list[dict]:
        """Every (sku, qty) this bin held, across the whole run."""

    # ── events ──
    def events(self, batch: int, aisle: int | None = None) -> list[dict]:
        """Timed picker events for one batch. Times are BATCH-RELATIVE."""

    def tasks(self, batch: int | None = None, aisle: int | None = None) -> list[dict]:
        """Per-task timing/effort rows — one picker's single-aisle pick sequence."""

    # ── rollups (sidecar when present, computed otherwise) ──
    def aisle_rollup(self, batch: int) -> list[dict]:
        """Per-aisle occupancy/pick/visit aggregates for one batch."""

    def top_skus(self, n: int) -> list[dict]:
        """The n most-picked SKUs, ranked."""

    def sku_series(self, skus: list[int]) -> dict:
        """Per-batch pick counts and home bin for the given SKUs."""

    def final_home(self) -> dict:
        """Per-SKU destination at the last keyframe — the colour authority."""
