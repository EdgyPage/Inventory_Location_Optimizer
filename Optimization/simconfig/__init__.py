"""simconfig — self-registering pick-config registry (the sim analogue of the graph suite).

Importing this package fires import_all() so every module under configs/ self-registers its
pick-config via @pick_config; Optimization/sim_config.py then rebuilds STORE_CONFIGS /
FULFILLMENT_CONFIGS from the registry (filtering by channel + enabled).  Adding a pick-config =
drop a module in configs/ — no central edit.  Whatif cell-matrix specs stay in whatif_config.SPECS
for now; the same registry pattern can absorb them later.
"""
from Optimization.simconfig.core.registry import (   # noqa: F401
    PickConfigSpec, pick_config, PICK_CONFIGS, PICK_CONFIG_BY_KEY,
)
from Optimization.simconfig.core.discovery import import_all

# Populate the registry on import so a spawn worker (which re-imports sim_config transitively)
# repopulates it in its own process before any job runs.
import_all()
