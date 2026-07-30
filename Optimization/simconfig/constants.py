"""Leaf constants shared by the pick-config modules AND sim_config.

Kept here (importing nothing from sim_config) to break the circular-import trap: the config
modules under configs/ need the per-channel picker-pool sizes, but importing them from sim_config
would cycle — sim_config imports the simconfig registry, which import_all()s the config modules.
sim_config re-imports these so `rs._STORE_PICKERS` / `K_PICKERS` and CONFIG['channels'][*]
['num_pickers'] keep their single source of truth.
"""

_STORE_PICKERS = 25      # machine order-picker pool size (store channel)
_FF_PICKERS    = 20      # human-walker pool size (fulfillment channel, independent of store)
