"""Shared over-time metric specs for the comparison graphs.

The same five trajectories drive faceted/, overlay/, and top/ (per-config) and the
cross-profile aggregate suite.  `agg=True` appends the '(× baseline)' unit used when the
curves are baseline-normalized ratios rather than raw values.
"""
from Performance_Evaluations.common.style import _TOP_DIMS


def overtime_metrics(agg=False):
    unit = ' (× baseline)' if agg else ''
    return [
        dict(x='task_batch', y='task_median', blo='task_p25', bhi='task_p75',
             f='task_duration_over_time', t='Task duration over time (median + IQR)',
             yl='task duration' + unit),
        dict(x='task_batch', y='task_mean', blo=None, bhi=None,
             f='avg_task_duration_over_time', t='Average task duration over time',
             yl='mean task duration' + unit),
        dict(x='batch', y='thr', blo=None, bhi=None,
             f='throughput_over_time', t='Throughput over time (items / sim-time)',
             yl='throughput' + unit),
        dict(x='task_batch', y='prod_hours', blo=None, bhi=None,
             f='production_time_over_time',
             t='Production time (total task time per batch, sim units)',
             yl='production time (sim units)' + unit),
        dict(x='batch', y='sigma_fd', blo=None, bhi=None,
             f='layout_travel_over_time',
             t='Layout travel cost over time (total f*D, lower=better)',
             yl='total f*D' + unit),
    ]


def top_tag(top_n, top_by):
    return f"top{top_n}" + (f"_by_{top_by}" if top_by in _TOP_DIMS else "")
