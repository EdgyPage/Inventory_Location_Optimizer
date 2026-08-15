"""connect.py — the three ways this project should open a SQLite file.

Before this module there were **seven** distinct connect idioms across 35 production call sites
and no shared helper, which had two concrete consequences:

  * `Optimization/run_whatif_delta.py` and `run_whatif_labor.py` opened **archived** sim DBs
    read-WRITE with no pragmas, dropping `-wal`/`-shm` sidecars beside a 1 GB file — exactly the
    condition `scripts/archive_cells.py::_quick_check` exists to defend against.
  * A shape fingerprint is only meaningful if reading a database cannot change it, and a
    read-write open can (WAL mode is itself a write).

Three intents, named — plus the one way to hand a writer back:

    read_only(path)    a finished artifact.  Cannot mutate the file, so it is the only safe way
                       to fingerprint one.
    writer(path)       a database being written during a run: WAL + a busy timeout, so parallel
                       workers do not trip over each other's checkpoints.
    bulk_writer(path)  a DERIVED file being rebuilt from scratch.  Durability pragmas off,
                       because a crash means "rebuild it", not "lose data".
    close(con)         finish with a writer and leave no `-wal`/`-shm` beside it.

Stdlib only; no imports from anywhere else in the repo.
"""
from __future__ import annotations

import os
import sqlite3

#: Shared by the writer idioms.  256 MB; the largest DBs here are ~2 GB.
_CACHE_PAGES = -262144

#: Parallel strategy workers checkpoint at the same time; 60 s of headroom avoids a spurious
#: "database is locked" when two 10-batch flushes coincide.
_BUSY_TIMEOUT = 60.0


def _uri(path: str, *, immutable: bool = False) -> str:
    """A read-only URI that survives Windows paths.

    `sqlite3` wants forward slashes in a URI even on Windows; a raw backslash path silently
    fails to open. That bug is why this is one function and not five copies.
    """
    p = os.path.abspath(path).replace(os.sep, '/')
    mode = '?mode=ro&immutable=1' if immutable else '?mode=ro'
    return f'file:{p}{mode}'


def read_only(path: str, *, row_factory: bool = True, immutable: bool = False):
    """Open a finished artifact strictly for reading.

    Never use a writer here: `PRAGMA journal_mode=WAL` is a WRITE, so it fails on a read-only
    mount and otherwise leaves sidecar files next to an archived database.

    `immutable=True` additionally promises the file will not change while open, which lets SQLite
    skip locking entirely — correct for an archived run, wrong for one still being written.
    """
    con = sqlite3.connect(_uri(path, immutable=immutable), uri=True)
    if row_factory:
        con.row_factory = sqlite3.Row
    return con


def writer(path: str, *, timeout: float = _BUSY_TIMEOUT, tuned: bool = False):
    """Open a database that a run is actively writing.

    WAL allows concurrent readers alongside the single writer, which is what lets the viewer
    open a run that is still going.
    """
    con = sqlite3.connect(path, timeout=timeout)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    if tuned:
        con.execute(f'PRAGMA cache_size={_CACHE_PAGES}')
        con.execute('PRAGMA temp_store=MEMORY')
    return con


def bulk_writer(path: str):
    """Open a DERIVED file for a full rebuild.

    Durability is deliberately off. It is safe precisely because the file is derived: a crash
    leaves a cache with no completion marker, which its reader treats as absent and rebuilds.
    Do not use this for anything that is a matter of record.
    """
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    con.execute('PRAGMA temp_store=MEMORY')
    con.execute(f'PRAGMA cache_size={_CACHE_PAGES}')
    return con


# ── closing a writer ────────────────────────────────────────────────────────────

def close(con, *, checkpoint: bool = True) -> None:
    """Close a connection, folding its WAL back into the database file first.

    WHY: one archived sweep carried **572** stray `-wal`/`-shm` files.  They are harmless to
    correctness, but they are counted as undeclared paths by `runschema.preflight.verify`, they make
    an archived copy differ from its original, and `scripts/archive_cells.py::_sweep_sidecars`
    exists solely to delete the ones that get that far.

    WHAT ACTUALLY PRODUCES THEM — measured, because the intuitive answer is wrong.  "SQLite removes
    them when the last connection closes cleanly" is true, but an unclean close is NOT the main
    source: a dropped handle, and even a killed worker, still gets finalized by CPython and leaves
    nothing behind.  The dominant producer is a **read-only** connection — opening a WAL database
    `mode=ro` CREATES the pair and cannot remove them, so an archived run accumulates sidecars just
    by being plotted, fingerprinted or opened in the viewer, long after every writer is gone.  No
    write-side change can eliminate those, and `_sweep_sidecars` stays the answer for them.

    What closing through here fixes is the narrower, real case: folding the WAL back in when this
    writer is genuinely the file's last, so a finished artifact is a single file.  Measured on a
    `--profile tiny` sweep — 241 finished DBs, zero sidecars, against 249-291 in each archived run.

    TWO PROPERTIES THIS DELIBERATELY HAS, because the obvious implementation has neither:

      * **It cannot lose data on a crash.**  A checkpoint only ever moves already-committed frames
        from the WAL into the main file and fsyncs them; it is a durability *increase*.  Crash
        mid-checkpoint and the WAL is still there and recovery replays it, exactly as before.  Note
        it does NOT commit for you — an open transaction is rolled back by the close below, which
        is plain `sqlite3` behaviour and must stay visible rather than be papered over here.
      * **It cannot stall the hot path.**  `wal_checkpoint(TRUNCATE)` normally waits for every
        reader to finish, and this connection carries a 60 s busy timeout, so the naive version
        could block a finishing worker for a minute while the viewer holds the file open.  The
        busy handler is disabled for the checkpoint alone: it then either succeeds immediately or
        reports busy and is skipped.  Skipping costs a sidecar, which is the outcome we already
        have; blocking would cost a minute of every worker's wall-clock, which is worse.

    `checkpoint=False` closes without any of this — for a `bulk_writer` (journal_mode=OFF, so
    there is no WAL) or a `read_only` handle, where the pragma is a no-op or an error either way.
    Safe to call on any connection: nothing here raises.
    """
    try:
        if checkpoint:
            try:
                con.execute('PRAGMA busy_timeout=0')       # never wait on a reader; see above
                con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except sqlite3.Error:
                pass                                       # read-only, or busy — the close still runs
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
