# Cap the door team

Type: task
Status: resolved

Graduated 2026-09-10 from
[Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md),
decisions 3 and 4. AFK build. Skills: `codebase-design`; `test-developer`. Independent of the
site-dock coupling (the cap is trailer physics and lives in `_unload_split`, which the coupling
keeps); it must land before the re-verification (26) so the gate runs under the declared physics.

## Question

Not a decision: the build side of 25's cap. Today `_unload_split`
(`Warehouse/inventory/inventory_reorder.py`) deals the whole crew across staged trailers with
`partition(crew, len(alive))`, uncapped, so one door with 22 receivers unloads at the crew's
full rate and doors are bookkeeping at every count. 25 declared the physics: **at most ten
receivers support one trailer's unload and pack**, every worker additive, even splits across
staged trailers, the steps not modelled.

What lands:

- A declared knob, `INBOUND_DOOR_TEAM` (settings default `None` = uncapped, byte-identical to
  today), carried on `PILOT_RUN_DEFAULTS` at 10 and stated in every `phase2_inbound_axis` entry
  like the other inbound keys (`_inbound_axis` refuses a partial entry). All five seams
  (memory `config-knob-has-five-seams`), provenance `declared` on the record.
- The dealing rule truncates at the cap: `partition` stays even, each team is cut to at most
  `cap`, and the freed-team reassignment steps (1)-(3) respect it, so a worker idles when every
  staged trailer is at its cap. Applies in BOTH allocation modes -- the cap is a property of the
  trailer, not of the dealing rule -- so `merged` with a cap is one team of `cap` on one trailer.
- **Receiver utilization is a reported metric, not a re-derivation** (25 decision 4): the crew
  stays as `staffing.py` derives it; the yard scorecard and the equilibrium report's receiving
  row gain the crew's busy share (receiver-seconds charged over crew x day) beside the load-based
  utilization, and the dock's parallelism ceiling `cap x doors` over the crew, no band.

Constraints:

- **Byte-identical with the cap off**, drain by drain, the way 15 proved the spread-zero path
  (memory `lockstep-tests-compare-aggregates-only`). The non-standing-yard path never reads the
  knob.
- The cap does not enter the coverage record (department-calibration 36 decision 3 stands: the
  record stamps the unconstrained lead; the dock's excess is the campaign's effect).

Acceptance: a three-trailer, 22-receiver scenario at cap 10 deals 10/10/2 in dock-priority order
and, when the third empties, reassigns its team to the top-ranked trailer with room under the
cap and idles the remainder; the lockstep proof above; the busy-share row renders on the
reference pair.

## Answer

Resolved 2026-09-11. **BUILT** — the door-team cap is declared physics, and the dock now reports
what it can seat. Everything the ticket named is code, with one correction to the ticket itself
and one defect found by rendering on a real run rather than trusting the unit tests.

### The rule, and the acceptance line that contradicted it

**The ticket's acceptance example is wrong and was not implemented.** It asks for "a three-trailer,
22-receiver scenario at cap 10 deals 10/10/2", which is a GREEDY fill. That contradicts both 25's
decision 3 ("even splits across staged trailers") and this ticket's own rule statement (`partition`
stays even, each team is cut to at most the cap): `partition(22, 3)` is 8/7/7 round-robin, every
team is already under 10, and the cut changes nothing. The two cannot both be built.

The DECLARED rule was built and the example corrected. Measured, not argued:

| receivers | staged trailers | cap | dealt | idle |
|---|---|---|---|---|
| 22 | 3 | 10 | 8 / 7 / 7 | 0 |
| 22 | 2 | 10 | 10 / 10 | 2 |
| 22 | 2 | none | 11 / 11 | 0 |

So the cap binds only when the EVEN division would exceed it, and 25's "at cap 10 the site's 22
receivers need three doors to be fully dealt" is true under this rule as well as under the greedy
one — which is presumably how the two survived side by side. The difference matters for the
campaign: a greedy deal makes the cap bind at every door count and turns dock priority into a
capacity grant, which is the lever 25 ruled off the route.

### What landed

- **`INBOUND_DOOR_TEAM`**, default `None` = uncapped. All five seams (memory
  `config-knob-has-five-seams`); the inbound family crosses as ONE record keyed off
  `sim_config.INBOUND_KEYS`, so membership in that list IS seams 3-5, and the existing family
  tests in `Tests/unit/test_inbound_params.py` already pin the flag and its CONFIG default.
  Carried on `PILOT_RUN_DEFAULTS` at 10 (`PHASE2_DOOR_TEAM`) and stated in every
  `phase2_inbound_axis` entry — `None` on the `inb_off` anchor, for the same reason
  `lead_spread` clears there: `inbound_spec()` refuses the knob without the standing yard, so
  an anchor inheriting it would raise at spec build rather than run.
- **Two refusals** at the spec seam, both the "half-read knob" pattern the policies already use:
  a cap without `INBOUND_STANDING_YARD` (v1's `release()` hands the whole drain over at once and
  never deals teams, so the run would complete uncapped under the cap's name), and a cap below 1.
- **The dealing rule** (`_unload_split` / `_unload_merged`, `Warehouse/inventory/inventory_reorder.py`).
  Read once in `_receive` and passed to both, because the cap is a property of the TRAILER, not of
  the dealing rule: `merged` under a cap is one team of `cap` on one trailer.
- **The reassignment spreads instead of handing the team to one target.** This is the part with
  teeth. Steps (1)-(2)-(3) became a ranked LIST, each target taking up to its own room under the
  cap. Step (3) is why: the pre-cap code extended an existing team unconditionally, which under a
  cap would put twice the cap on one trailer. Uncapped the room is unbounded, so the head of the
  list takes the whole team and the off path is byte-identical rather than merely equivalent.

**One thing was built and then removed.** The first cut gave the workers cut by the cap an IDLE POOL
re-offered at every reassignment. It is unreachable, and provably: the pool is non-empty only when
the even deal exceeded the cap, which (sizes differ by at most one) means every team is exactly
the cap; a freed team is then exactly the cap, and the only target with room is a FRESH staging
whose room is also exactly the cap. So a cut worker can never be seated later, and the pool was
dead code dressed as a safety net. Removed, and the invariant it was protecting — a worker idles
only while every staged trailer with work is at its cap — is what the spread already gives.

### Reported, never re-derived (25's decision 4)

The crew stays as `staffing.derive` sizes it. Two read-outs were added, neither a Quantity and
neither banded:

- **Dock ceiling** = cap x doors / crew, on `EvalContext.dock_ceiling()` — the share of the
  derived crew the dock can seat AT ONCE. It rides the yard scorecard as a column and the audit's
  RECEIVING ROW as a suffix on the band reading, printed whether or not it binds: "the dock could
  seat everyone" is the fact that makes a below-band receiving read damning rather than explained.
  `None` on an uncapped run, with no settings fallback — unlike the fee threshold, whose default is
  a real number, `None` is honest both for a run that chose uncapped and for every run that
  predates the knob.
- **Receiver busy share** = receiver-seconds over crew-days, on the scorecard beside door
  utilization rather than instead of it: a capped dock can saturate its doors while the crew idles,
  and saturate the crew with doors to spare.

Both values are stamped onto `sim_result` by `_sim_result_from_meta`, because CONFIG is not a
channel to a SPAWNED analysis worker (memory `config-is-not-a-channel-to-an-evaluation`) — the same
seam the fee threshold's derive-late report needed at 18.

### THE DEFECT THE REAL RUN CAUGHT, and it would have shipped

The busy share first rendered at **184%**, which is impossible. The unit tests passed; the
arithmetic was wrong. `_arm_span_days` measures the arm's CALENDAR span, and the sim clock only
advances through working hours — 40 work days on an 8-hour day span 13.3 calendar days, so a crew's
seconds over the calendar span over-reads by exactly 86400/28800 = 3. It is the right denominator
for DOOR utilization (door spans are calendar time) and the wrong one for a crew grant, and the two
read-outs sit in adjacent columns of one table. Now denominated on DISTINCT `work_day` values times
crew x day_seconds, the same grant `equilibrium._utilization_clause` measures against. The test was
rewritten so the two denominators disagree in the fixture and only the work-day one satisfies the
arithmetic.

**Verified against the reference pair**, `comparison_20260910_173151` (era, yard on, uncapped,
4 doors recorded) — which also exercises the pre-cap vintage path:

| leaf | door util | receiver busy | audit's realized recv | dock ceiling |
|---|---|---|---|---|
| store | 57% | 61% | 0.608 | uncapped |
| fulfillment | 15% | 16% | 0.165 | uncapped |

The two agree to the rounding while coming from DIFFERENT source columns — the scorecard reads
`batch_stats.recv_seconds`, the audit reads `work_events` where the role is `receive` — so this is
a cross-check, not a restatement. Both figures re-rendered with `--only`, so nothing else in that
run was touched.

**One fix the ceiling forced:** the scorecard derived its door count as `max(free_doors_start)`,
which is a LOWER bound on a resumed arm, while the ceiling is computed from the count the run
RECORDS. Two different door counts in one table. The column now prefers the recorded value and
falls back to the derivation only for runs that predate the recording, exactly as the fee
threshold does.

### Constraints held

- **Byte-identical with the cap off**, drain by drain — records, queue stream, both ledgers,
  transit census and carryover — in BOTH allocation modes, plus a cap above the crew size, plus
  the non-vacuity that a cap of one DOES move the labour stamps while moving neither the queue
  stream nor a ledger (the containment property: the handoff stays canonical). The v1 transit has
  no `door_team` attribute at all, so the non-standing path is untouched by construction.
- **The cap does not enter the coverage record.** Nothing under `Optimization/simconfig/staffing.py`
  or the coverage loop was touched; department-calibration 36 decision 3 stands.
- Both halves of the rule were proven to FAIL by sabotage: removing the truncation breaks two
  tests, removing the reassignment room check breaks two others.

### Gates

`Tests/unit` 2002 green (23 new in `Tests/unit/test_door_team_cap.py`);
`Tests/e2e/test_standing_yard_e2e.py` 4 green, which is the run-level degenerate lockstep against
v1. Run-tree preflight re-proved the tree shape UNCHANGED and refreshed the source fingerprint
(`strategy_runner.py` is a shape source) — no schema event, contract `5c9bc35db55b` holds. Context,
memory, path and docref guards green; profile-tree current.

**Owed:** the derived architecture layer, to the `architecture-maintainer` agent — as at 09, 13, 15
and 21.

### Glossary

`CONTEXT.md`: *Door team* gains the even-then-cut rule; *Dock ceiling* and *Receiver busy share*
added.
