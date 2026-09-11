# Cap the door team

Type: task
Status: open

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
