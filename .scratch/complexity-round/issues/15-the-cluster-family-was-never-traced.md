# 15 - the cluster family was never traced, and both cells are quadratic

Type: finding
Status: resolved

## Context

Ticket 09 records that a strategy axis was needed because every `--config` cell ran the SAME
travel-balanced arm. Two more cells were missing for the same reason, and they hid more:
`_ClusterMapPool` and `_CoDemandPool` appear in NO archived artifact at all. Not small - absent.
The deep ladder's slowest arm at every rung belongs to that family.

Added `--config cluster_map` (`uni_cluster_map_norsl`) and `--config cmin` (`uni_cmin_norsl`).

## Answer

**Both convict.**

`cluster_map`: `_cluster_map_choose_aisle` makes four O(|live|) passes per placement, and the
tracer can see NONE of them - a comprehension is one frame entry however wide the scan is. Only
`_closest_abs` shows, at k = 2.39 with local exponents 1.34 -> 2.81, still steepening. The real
scan volume, measured by wrapping the function on the ladder's own workload:

| max_skus | calls | mean \|live\| | Sum \|live\| | Sum \|tied\| (visible) |
|---|---|---|---|---|
| 500 | 4,191 | 7.0 | 29,337 | 1,795 (6.1%) |
| 8,000 | 76,514 | 106.4 | **8,141,090** | 1,174,077 (14.4%) |

Calls k = 1.02, width k = 0.97, product **k = 1.99** with local exponents pinned at 2.04. The
instrument under-reported by 7x and it was still the top offender in the cell.

`cmin`: `_build_aisle_score_fn.<locals>.assign.<locals>.score_of` at **9,195,611 calls, k = 1.98**,
and `_delta_lift_from_row` at 7,997,764, k = 1.97. `t_reord` shows it as a section wall at k = 1.78.

**A methodological warning worth more than the finding.** The first width measurement drove
`run_fullfid` and reported 676 calls at 4,000 SKUs where the ladder recorded 37,911 - a different
entry point on a different workload. It also reported mean |live| = 2.5, which would have
ACQUITTED the candidate outright. It was caught only because the call count could be compared
against the ladder's own. A width instrument must run the ladder's workload through the ladder's
own configuration, not a plausible-looking neighbour.

-> the fused pass is ticket 16; the structural half is ticket 17.
