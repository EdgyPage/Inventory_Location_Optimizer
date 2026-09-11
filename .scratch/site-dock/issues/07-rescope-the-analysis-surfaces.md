# Re-scope the analysis surfaces to the site

Type: grilling
Status: open
Blocked by: 03

HITL. Skills: `grilling`. Blocked by
[Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md): every surface
here reads whatever scope that ticket creates.

## Question

Three analysis surfaces assume a channel leaf owns its inbound. Decide what each becomes.

1. **The yard views.** `Optimization/Performance_Evaluations/yard/{binding,detention,fee,scorecard}.py`
   (`binding.py:83-92`, `detention.py:112-117`, `fee.py:74-99`, `scorecard.py:41-86`,
   `__init__.py:30`) are per arm within one leaf, reading trailer and drain frames out of that
   leaf's DBs and denominating door utilization by `_arm_span_days` (`scorecard.py:45-54`). With one
   site yard the frames belong to a site scope and the span denominator is ambiguous between two
   leaves' clocks — a scope change, not a regroup. Memory `calendar-span-is-not-work-days` is the
   live trap: denominate on distinct `work_day` values, not a calendar span, or a utilization
   over-reads ~3×. Memory `a-right-site-total-hides-two-wrong-shares` is the second: check the
   per-channel split separately, because a correct site total can hide two bands failing in
   opposite directions.

2. **The equilibrium report's receiving clause.** `Diagnostics/equilibrium_report.py:49-81` reaches
   a verdict per `(cell, pair, config, channel)` leaf via `expectations_for(staffing, pair=,
   channel=)` (`equilibrium.py:332`); the receiving and put clauses (`:390-397`) already compare a
   leaf's load to a site crew. Coupled, evaluate the receiving clause **once at site scope**, and
   decide the same for put (ticket 04 owns the number, this ticket owns the report). Memory
   `equilibrium-check-two-traps` applies: `released_late` belongs to the CAPPED day before it, and
   the self-check must re-price rows rather than average them.

3. **`run_channel_rollup.py`.** The charter settles the outcome — **refuse a coupled run, keep the
   script for inbound-off runs**. What is open is the mechanics: `run_channel_rollup.py:11`,
   `:116-153`, `:180-226` states independence as its validity argument, so decide what it reads to
   detect a coupled run (ticket 03's marker), what it says when it refuses, and whether the
   docstring's argument is rewritten or simply scoped.

4. **The figure views.** Figure views are derived from shape and quantities (memory
   `figure-views-are-derived`), so a site-scoped quantity may render a view nobody declared. Decide
   whether any new site quantity needs a declared view here or whether that waits for the campaign —
   and route any new quantity through the declaration, never an ad-hoc renderer (the
   `route-reviewer-finding` skill is the precedent).

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§3.
