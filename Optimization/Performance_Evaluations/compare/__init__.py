"""Shared over-time metric specs for the comparison graphs.

The same five trajectories drive faceted/, overlay/, and top/ (per-config) and the
cross-profile aggregate suite.  `agg=True` appends the '(× baseline)' unit used when the
curves are baseline-normalized ratios rather than raw values.
"""
# The specs/tag MOVED to common/painters.py (the shared painter surface); re-exported
# here because this package is their primary consumer and its modules import them as
# package-local vocabulary.
from Optimization.Performance_Evaluations.common.painters import overtime_metrics, top_tag  # noqa: F401


