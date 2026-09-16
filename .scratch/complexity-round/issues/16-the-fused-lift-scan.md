# 16 - four passes become one in `_cluster_map_choose_aisle`

Type: refactor
Status: resolved

## Context

Ticket 15 measured this function at k = 1.99. Per placement it built `live`, rebuilt `lifts` onto
it, took a `max`, then re-filtered for `tied`.

## Answer

**Landed byte-identical, commit `8a5c6185`.** `best` is still the first maximal value under `>`
(so NaN is skipped exactly as `max` skipped it) and `tied` is still in `by_aisle` order, which
the `min(tied, ...)` tie-break depends on because `min` returns the FIRST element achieving the
minimum.

Measured on the same ladder - every count identical, including `_closest_abs` (1,174,077) and
`_delta_lift_from_row` (462,913), so the same aisles tie and the same tie-breaks run:

| max_skus | wall before | wall after | |
|---|---|---|---|
| 500 | 0.67 s | 0.45 s | -33% |
| 4,000 | 8.48 s | 6.09 s | -28% |
| 8,000 | 20.43 s | 13.55 s | **-34%** |

(1k and 2k were contended by a concurrent unit run and are not quoted.)

**THE CLASS IS UNCHANGED** and the docs say so rather than implying otherwise. This is a constant
factor - a third of the arm's wall for removing three passes over a list. The quadratic is ticket 17.

The equivalence test is new because the frozen oracles were nearly no evidence: they reach this
function 1,507 times and **1,503 of those take the `lifts is None` straggler branch**, not the pool
branch that carries the quadratic. Its fixture needed two corrections before it could observe what
it claimed to test, and the complexity guard beside it needed three - all three of those were
errors in the INSTRUMENT, not in the code under test (see `10e23697`).
