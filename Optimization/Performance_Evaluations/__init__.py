"""Performance_Evaluations — registry-driven analysis/graph system.

Each graph/analysis is a small self-registering module under one of the category
subpackages (per_strategy/, comparison/, breakdown/, stats/, aggregate/).  A module
declares an Evaluation via the @evaluation decorator (core/registry.py); importing the
package walks every submodule so the decorators fire and populate the registry.

The driver (driver.py) builds an EvalContext once per config (and an AggregateContext
per cross-profile group), then runs the evaluations named by a preset (presets.py).
Adding a graph = drop a file here + add its key to a preset; no central script to edit.

matplotlib's Agg backend is set HERE, before any submodule imports pyplot, so every
worker process (Windows spawn re-imports this package) is headless-safe.
"""
import matplotlib
matplotlib.use('Agg')   # must precede any pyplot import in the graph modules

from Performance_Evaluations.core.discovery import import_all

import_all()            # fire every @evaluation so the registry is populated on import
