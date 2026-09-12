# Land the draw probability through every closed form

Type: task
Status: resolved  <!-- closed OUT OF SCOPE -->
Blocked by: 41

Graduated 2026-09-12 from
[Close the fulfillment fill-law gap](38-close-the-fulfillment-fill-law-gap.md), decisions 4, 5, 8
and 10. The build. **Do not start until
[Gate the form on the generator](41-gate-the-form-on-the-generator.md) has PASSED** -- it exists
so this commit is never written against a form that cannot reproduce the generator.

## Question

Replace the line share with the draw probability everywhere the record reads a per-SKU rate, in
ONE commit, era-only.

**The swap.** `d_s = n * pi_s * E[q_s]` becomes `n * p_s * E[q_s]`, and `p_s` reaches
`Optimization/simconfig/coverage.py` as an ARGUMENT -- that module "imports no CONFIG and touches
no file" (`coverage.py:68`) and stays that way. Every consumer moves together: `daily_demand`,
`expected_travel`, `staffing`, and the fragmentation transient (`fragmentation.
section_fragmentation` already takes a rate through `lines_per_day_by_sku`). Two per-SKU demand
rates in one record is the failure this map has paid for twice (memories
`cut-is-a-level-not-a-flow`, `carryover-two-producers-one-key`), so a partial swap is not an
acceptable intermediate state even temporarily.

**The N law rides inside it.** `N ~ Binomial(K, p_s)` replaces the compound Poisson in
`_served_under_lead` (`coverage.py:350`). Panjer covers the whole (a,b,0) class, so this is a
parameter change: `a = -p/(1-p)`, `b = (K+1)p/(1-p)`, `g(0) = (1-p)^K`; `f(0) = 0` already holds.
It is not optional under this form -- `p_s` is a probability, and a Poisson parameterised by one
breaks outright as `p_s` approaches 1, which is where the busy fulfillment SKUs now live. State
the `releases_per_day > 1` limitation where `coverage.py:376` pins the supplier-lead rounding:
pinned to the era, not solved.

**Era-only; flag-off byte-identical.** `coverage` runs in every mode (ADR-0002), so this must be
gated on the era or it moves flag-off stock levels too. Prove the neutrality on DB ROWS against a
`git archive HEAD` copy, not on figures -- figures are not byte-reproducible (memory
`figures-are-not-byte-reproducible`, a HEAD-vs-HEAD control differed on 51/51 PNGs) -- and strip
the wall-clock columns. Use `git archive HEAD | tar -x` rather than `git worktree add`
(memory `head-copy-via-git-archive`).

**The rebuild guard (38 decision 10).** `declare_from_record` recomputes the share from the
catalogue (`Optimization/simdriver/era_coverage.py:346`), so without a guard every historical
rebuild would field a different warehouse with nothing stamped to detect it. The artifact from
[Characterise the draw probability](40-characterise-the-draw-probability.md) travels in the pair
directory (so a `COLD_DRIVE` rebuild resolves it), and a derivation identity rides the record so a
rebuild that would produce a different `p_s` REFUSES -- the `floors_at` / `holds_at` pattern, and
the refusal is the important half. `run_analysis` and `run_map_precompute` both go through this
path.

**Expect the floor solve to move, and let it.** Today 100% of both sections sits at the line
floor, so `coverage_days`, the lead and `safety_days` bind on nothing; the busy SKUs should lift
OFF the floor. Fulfillment sits at floor 1.267 against a 0.0253 target with no slack, so
`solve_floor_lines` (`coverage.py:521`) will buy enough shelf to survive a SECOND prior line. If
it refuses at `_MAX_FLOOR_LINES` (128, `coverage.py:84`), do NOT work around it: that is the one
trigger for 38's decision 4 -- the confidence declared per channel, as an amendment to ADR-0004 --
and it is a decision, so stop and raise it.

Done when the swap is committed on `develop` with the schema pipeline ridden properly
(`--sync` before a DDL edit, `--accept` after; CLAUDE.md 2), tests green on the narrowest
covering subset, and the flag-off neutrality proven row for row. Report the Binomial increment
separately from the rate increment even though they ship together (38 decision 5).

**Method warnings:**
- A config knob has FIVE seams, and the fifth is `workunits._shared` -- skipping it silently
  reverts the knob to its default in every spawned worker (memory `config-knob-has-five-seams`).
- A run-level value an evaluation needs must be stamped onto `sim_result` in
  `_sim_result_from_meta`; CONFIG stops at the parent (memory
  `config-is-not-a-channel-to-an-evaluation`).
- Resolve run-tree paths through `runschema.resolver_for`, never by joining strings or reading
  directory names positionally.

## Closed: OUT OF SCOPE, 2026-09-12

Ruled out of scope by user decision while resolving
[Re-take the reference run under v3 and re-establish the gap](46-retake-the-reference-run-under-v3.md), which measured the fill-law gap CLOSED under the
era's declared sampler: 12 arms judged, **0 failed**, fulfillment supply 0.1044 -> 0.0284
against an expected 0.0251 at tol 0.020. The v2 sampler's duplicate draws were 95.5-97.1% of
the gap this chain existed to explain.

Nothing to land: 41 is out of scope and no corrected form exists. Landing `p_s` through the
closed forms would move every level and buy a seventh comparability break to correct a
residual the equilibrium instrument already passes (+0.0033 of a 0.020 band).

A scope boundary, not a step on the route: this ticket is NOT in the map's Decisions-so-far.
It returns only if the destination is redrawn.
