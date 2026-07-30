"""Auto-discovery: import every simconfig submodule so @pick_config decorators fire.

Mirrors Optimization/Performance_Evaluations/core/discovery.py.  Used instead of a hand-maintained
import list so dropping a new pick-config file into configs/ registers it with zero edits elsewhere.

Critical for multiprocessing: the package __init__ calls import_all() at import time, so each
spawned worker that (transitively) imports Optimization.simconfig repopulates the registry in its
own process before any run.
"""
import importlib
import pkgutil


def import_all() -> None:
    from Optimization import simconfig as pkg
    for mod in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + '.'):
        # skip self to avoid a redundant re-import of this module during the walk
        if mod.name.endswith('.core.discovery'):
            continue
        importlib.import_module(mod.name)
