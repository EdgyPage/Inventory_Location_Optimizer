"""Auto-discovery: import every submodule so @evaluation decorators fire.

Used instead of a hand-maintained import list so dropping a new graph file into a
category subpackage makes it available with zero edits elsewhere.  Walk order is
irrelevant — execution order comes from the preset's `keys` list, not import order.

Critical for multiprocessing: the package __init__ calls import_all() at import time,
so each spawned worker that does `import Performance_Evaluations` repopulates the
registry in its own process before any job runs.
"""
import importlib
import pkgutil


def import_all() -> None:
    import Performance_Evaluations as pkg
    for mod in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + '.'):
        # skip self to avoid a redundant re-import of this module during the walk
        if mod.name.endswith('.core.discovery'):
            continue
        importlib.import_module(mod.name)
