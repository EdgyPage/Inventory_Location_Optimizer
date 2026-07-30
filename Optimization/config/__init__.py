"""Optimization.config — the run's tunable inputs, in one place.

CONFIG (sim_config) is the single source of truth for every knob; `channels` describes the two
independent warehouse sections; `strategies` is the assignment-function registry; `whatif_config`
holds the cell-matrix specs.  See README.md for what does and does not belong here.

sim_config and channels import each other and must stay co-located.
"""
