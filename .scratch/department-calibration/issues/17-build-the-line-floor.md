# Build the line floor

Type: task
Status: resolved
Blocked by: 16, 18

Graduated from [Choose the coverage floor](15-choose-the-coverage-floor.md), decisions 1-6 and
8. AFK build, flag-off byte-identical. Skills: `codebase-design` (the floor is a change to
`coverage.stock_levels`' contract), `domain-modeling` (the glossary entries land with it).

## Question

Replace the unit floor in `Optimization/simconfig/coverage.py` with the line floor, read off
the stamped line distribution:

- `L_s = ceil(floor_lines x line.mean())`; `Q = max(round(coverage_days x d_s), L_s)`;
  `rp = min(Q - 1, max(round(d_s x (lead + safety_days)), L_s))`. A floored SKU therefore runs
  base-stock (`rp = Q - 1`).
- **`floor_lines`** (1.0, `assumed`) on `STAFFING_KEYS` and all five seams (`--floor-lines`),
  beside `coverage_days` / `safety_days`, whose defaults stay 10 / 2.
- **The record** (`calibration[<pair>].coverage.final[<channel>]`): the floor stats become
  line-denominated (`floor_line_skus`, `floor_line_share`, `floor_line_demand_share`,
  `realized_coverage_days` over the SKUs above the floor) and gain the demand-weighted
  **expected first-pass fill rate**, `sum d_s x E[min(X, S_s)] / sum d_s x E[X]`, per section.
- **`pipeline_qty`** stamped per SKU under the era (`round(d_s x lead)`), preferred by
  `_fire_reorders` when present, the `rp x lead / (lead + 1)` heuristic kept otherwise.
- `rescale_section` still resets `stock_plan`; the planner re-packs line-sized quantities.
- **Glossary**: *Stock coverage* rewritten (already done at resolution), *Line floor*,
  *Base stock*, *Line distribution* present.

Done when: the formulas are pinned by hand in `Tests/unit/test_coverage_rescale.py` (a floored
SKU's `rp = Q - 1`, an above-floor SKU's line-sized safety, the fill rate for a known Poisson);
the input rides every seam; the record carries the fill rate; flag-off is byte-identical; and
ONE 40-day era run on the reference pair, read through the equilibrium report after
[Narrow the drained clause to labour](18-narrow-the-drained-clause-to-labour.md) lands, shows put
and receiving utilization in band on both leaves, the store's realized missed share against its
stamped fill rate, and days drained -- with the store's answer recorded as "no wave: a trickle in
lockstep with picks". That run is a check of the build, never a calibration step.

## Answer

**BUILD LANDED 2026-09-06 (one session, AFK). The 40-day check RAN and did NOT read in band:
the floor is decided, stamped and priced correctly, but the warehouse neither fields it on the
fulfillment section nor keeps it on the store section -- three tickets graduated (19, 20, 21).**

**The build.**  `Optimization/simconfig/coverage.py`: `line_floor(line, floor_lines)` =
`ceil(floor_lines x E[line])` (>= 1), `pipeline_qty(d_s, lead)` = `round(d_s x lead)`,
`stock_levels(d_s, lead, coverage, safety, floor_units)` floors BOTH levels so a floored SKU
lands at `rp = Q - 1`; `rescale_section(..., floor_lines=)` stamps `Order.pipeline_qty` and
reports line-denominated shares (`floor_line_skus/share/demand_share`, `base_stock_skus/share`,
`floor_rp_skus` above the floor, `above_floor_skus`, `realized_coverage_days`); `fill_rate()`
is the expected FIRST-PASS fill rate, `Σ lines_s E[min(q, Q_s)] / Σ lines_s E[q]` with
`lines_s = n π_s` (the ticket's `Σ d_s x ...` carried an extra `E[X]` factor -- the
lines-weighted form is what "units served over units demanded" is), priced by
`era_coverage.fixed_point` ONCE on the PLANNED orders (the planner grows and shrinks levels)
into `coverage.final[<ch>]['fill']` with `expected_missed_share`, the post-plan base-stock
share and `pipeline_units`.  `floor_lines` (1.0) rides settings / CONFIG / `STAFFING_KEYS` /
`_SCALAR_DEFAULTS` / `--floor-lines`; the record, both restore sites and the payload follow by
construction.  **The stamp**: `Order.pipeline_qty` (a slot on every construction path) and
`cartons.pipeline_qty INTEGER` (NULL = not stamped) through the schema pipeline -- `inventory_db`
`4ff06991df47 -> 025f4b1548a9`, `--sync` before, the outgoing id vetted as
`PRE_PIPELINE_INVENTORY_SCHEMA_ID`, both older vintages served by `_cartons_override_sql`
(the prefix each has + NULLs), `inventory_semantics` tagged.  `Order.pipeline_allowance()` is
the ONE definition (the stamp, else the manager's `round(rp x lead / (lead + 1))` byte for
byte); `_fire_reorders` and `staffing.reorder_lot` read it.  `sim_assets.load_run_inventory
(path, era=)` clears the stamp flag-off at BOTH load sites -- the flag is EXPLICIT because a
spawned worker's CONFIG is pristine (`era_on()` there is the settings default; the worker
passes its payload's `drain_or_cap`).  Two derivation corrections the floor exposed:
`reorder_lot` under base stock is `max(E[line], P + 1)` (the old rule's answer was 1 = one
PACK per unit, a tenfold receiving over-count on a ten-unit line; exact at `P = 0`, which every
generated catalogue has); `staffing.derive` prices the pick load as SERVED units x `s_pick`
(= ρ by construction) -- it priced the script's DEMANDED units at a per-served-unit constant,
which read the crews at 0.98 with the charter's headroom gone (served and demanded were
interchangeable before the floor).  `equilibrium.expectations_for` surfaces `fill_rate` /
`expected_missed_share`; the missed-share clause carries `expected` / `delta` (never gates);
`summarize` and the audit's subtitle print it.  Glossary: *Staffing record* names the floor and
the fill rate.  **Note the arithmetic of `ceil`**: `E[line] = λ + e^-λ`, so `L_s = λ + 1` for
every Poisson SKU -- one pick's worth is one pick plus one unit (decision 2, "rounded up").

**Verification**: 1768 unit tests green (28 new or rewritten: the floor on both levels by hand,
base stock, the stamped pipeline through `_fire_reorders` and the file round trip, the
pre-pipeline vintage through its override, the fill rate for Poisson(2) at a shelf of 3 --
`(3 - 8e^-2) / (2 + e^-2)` -- and its line weighting, the three knobs' seams, the flag-off
strip at both load sites, the lot under base stock, the ρ identity); `test_profile_tree_golden`,
e2e `test_batch_precompute`; six fast gates green; full `preflight` (both canaries, tree
unchanged, fingerprint refreshed); `profile_tree --write` (new head `7de9027f83ab`);
`test_schema_identity`, `test_column_semantics`, `test_schema_compatibility` 212/213 (the one
failure is the stray `.claude/worktrees/peaceful-wiles-3853f3/` worktree, as in 16).  Code
review (report-only) found no critical issue; its four warnings are fixed above.  A 6-day
era smoke on a 4,000-SKU slice ran end to end with both audits rendered.

**The check** -- `comparison_20260906_222118` under `COMPARISON_OUTPUT_DIR`, `_canary_single`
(fifo, round_robin), 40 era days, the reference pair (pre-stamp vintage, loaded through its
override).  The fixed point closed in TWO rounds: with every SKU on the floor Q is a function
of the line law alone, so `n` has nothing to move.  Store 10,243,490 -> 2,510,168 units,
fulfillment 4,702,045 -> 1,844,275; both sections 100% on the floor and 100% base stock
pre-plan (decision 4, confirmed); stamped fill rates 0.905 store / 0.782 fulfillment (the
fulfillment number is the PLANNED shelf's, see below); put crew 75, receiving crew 24;
`daily_demand_units` (served) 5,708 / 29,910 -- and `fill_rate`'s `served_per_day` equals the
closed form's units to the unit, an independent cross-check of the two expectations.
"No wave" is confirmed on both sections: reorders fire every batch from day 1 in lockstep with
picks (store 1,250-2,250 placements a day).

*Fulfillment leaf (audit rendered)*: pick 0.985 realized against 0.976 recorded -- 0.85 once
`derive` prices served units, so ABOVE band; put 0.507 / 0.713 BELOW; receiving 0.209 / 0.226
in band; 1 of 40 days drained (39 capped on labour carry); missed share 0.663, trend +0.171,
against 0.218 expected; supply carry 88,857 units standing on day 39; demanded per batch grew
44,964 -> 125,162 while picked stayed ~32,000.  **Cause: the planner fielded 1,672,280 of the
1,844,275 units requested and left 48,466 SKUs (30.3%) BELOW their line floor** -- the traced
SKU (floor 13) holds four singleton units, is picked for exactly 4 every day, and its
rolled-over remainder grows 9 -> 157 units in 14 days.  A shelf below the line under base stock
is a treadmill; the record's planned-level fill rate (0.782 vs ~0.9 at the floor) is the number
that says so.  -> [Field the floor: the planner against the line](19-field-the-floor.md).

*Store leaf (audit RAISED)*: `InstrumentError` -- day 3 ended AT its cap with the last picker
138 s late and nothing standing, DRAINED under 18's labour-only rule, and the lag behind it is
the instrument contradicting itself.  36 of 40 days had overtime.  -> [Overtime behind a
drained day raises the instrument](21-overtime-behind-a-drained-day.md).  Read directly off the
sim DB (the three other clauses): 5 / 40 drained, 35 capped; pick 0.913 (in band of 0.85:
+0.063), put 0.291 against 0.134 ABOVE, receiving 0.590 / 0.623 in band; missed share 0.205,
climbing 0.127 -> 0.283 against 0.095 expected.  The store FIELDED its floor (Q/L 1.01, 7.9%
of SKUs below).  **Cause: put-away never adds to an occupied bin** (structural, memory
`empty-bin-preference-is-structural`), so every base-stock top-up of a partially taken bin
opens a second bin: 215,358 of 239,938 store SKUs held more than one bin by day 40, a line
served against a remnant misses, and the put crew places twice.  -> [Let a base-stock top-up
reach the shelf](20-let-a-base-stock-top-up-reach-the-shelf.md).

**What stands, what does not.**  The floor, the stamp, the fill rate, the seams and the two
derivation corrections stand, flag-off byte-identical.  The era's numbers on BOTH sections stay
PROVISIONAL: the check is to be re-read once 19 and 20 resolve and 21 lets the store audit
render.  The `.scratch`-external assets: the run's `run_spec.json` and both leaves' sim DBs
carry every figure above.
