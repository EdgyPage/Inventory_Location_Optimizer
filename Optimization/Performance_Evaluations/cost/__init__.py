"""cost.* evaluations — what a placement rule costs to RUN, in real wall-clock seconds.

The only family that measures the SIMULATOR rather than the warehouse.  Every other
family reports modeled seconds from the cost model — an effort figure for comparing
placement rules.  These report seconds a CPU actually spent, which is a different
question with a different audience: a WMS engineer asking whether a rule is cheap enough
to run per arriving unit, and whether it carries an offline job to schedule.

Run scope, because `runtime_metrics.db` lives at the run root and spans every cell — no
leaf and no per-cell aggregate can see it.
"""
