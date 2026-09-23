---
name: gain-evaluator-prices-by-the-policy-record
description: the gain evaluator never reads mgr.placement; it rebuilds the arm's pools from the static policy record, so a stock phase placed by another rule is priced wrongly
metadata:
  type: project
---

`strategy_runner._gain_bundle_for` builds a leaf's `GainBundle` from `_POLICY_BY_KEY[strat.restock]`
and pool factories over the manager's live `_aisle_*` dicts. It never consults
`mgr.placement`. So an unload plan is always priced as if the ARM'S OWN RULE places the
trailer, whatever rule actually does.

This is what made the uniform stock mode meaningless in a fill trial: placing the fill by the
manager's default (uniform-random) placement would have the evaluator optimise an objective
nothing enacts, and placing it by the arm's rule makes `uni_*` a copy of `opt_*`. The fill mode
(commit 7bcd0cec) refuses `uni_*` arms until the user decides (ticket 05, "THE OPEN DECISION").

**Why:** any future mode that places stock by a rule other than the arm's (a warm start, a
reslot policy, a mixed placer) inherits the same mismatch silently.
**How to apply:** before pricing unloads under a non-arm placer, check the bundle, not the
manager. Related: [[fill-trial-driver-mode]], [[placement-pools-and-the-audit-point]].
