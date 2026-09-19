"""_worker_dies_at_import.py -- a pool worker target whose MODULE refuses to import in a child.

The phase-2 launch of 2026-09-18 opened a 12-wide spawn pool over a working tree carrying a
half-applied edit; every child died at IMPORT, before it registered, and the driver hung for
37 minutes on the pool's exit (`.scratch/phase-2-campaign/issues/01`).  This module is that
child, on purpose: the parent imports it fine (so `worker` pickles by reference), and a
spawned child raises while unpickling the call item -- exactly where a broken tree kills a
real worker -- and exits with a traceback the parent never sees.

Not a test file (leading underscore); imported by `_broken_pool_driver.py` only.  The
environment flag is the driver's: it sets `BROKEN_POOL_DIE_IN_TARGET` AFTER its own import,
so the supervisor's import probe -- a fresh interpreter, not a multiprocessing child -- dies
here the way the pool's children do.
"""
import multiprocessing
import os

if multiprocessing.parent_process() is not None or os.environ.get('BROKEN_POOL_DIE_IN_TARGET'):
    raise RuntimeError('_worker_dies_at_import: this module refuses to import in a spawned child '
                       '(test fixture standing in for a broken working tree)')


def worker(sa):
    """Never reached in a child; exists so the parent has a picklable target to submit."""
    return {'done': True, 'strategy': sa['strategy']}
