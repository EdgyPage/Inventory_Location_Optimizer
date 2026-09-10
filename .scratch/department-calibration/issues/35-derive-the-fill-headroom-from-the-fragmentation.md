# Derive the fill headroom from the fragmentation

Type: task
Status: open
Blocked by: 34

Graduated 2026-09-10 from
[Band the own-bin share and the free-index depth](32-band-the-own-bin-share-and-free-index.md),
decision 7. AFK build. Skills: `codebase-design`; `schema-maintainer` if the record's shape
moves; `memory-maintainer` for the comparability break.

## Question

Not a decision: the build that makes 32's decision 7 true. Today `STORE_FILL` / `FF_FILL`
(0.85, `assumed`) stand where a closed form belongs: the planner sizes each bucket at
`ceil(requirement / (bins_per_aisle x fill))`, so 15% of every bucket is headroom nobody
derived, and the store's slide (32) ends somewhere that number was never chosen to cover.

What lands:

- Under the era the fill is DERIVED per bucket: `requirement / (requirement + E[extra])` from
  34's stamp, floored at a declared minimum headroom (a new staffing key on all five seams, the
  memory `config-knob-has-five-seams`; provenance `assumed`). The regime decides which keys are
  inputs (29): flag-off keeps the typed fill and records the derived one as None; the era
  refuses `--store-fill` / `--ff-fill` the way it refuses the picker flags.
- The sizing promise is re-stated: the warehouse holds the declaration AND its stationary
  fragmentation; `UnfieldableRequirement` names the extra bins short, not only the requirement.
- The fixed point (`era_coverage`) re-runs with the derived fill inside the loop, and the record's
  `fielded.buckets[]` carries `fill` beside `expected_extra`.
- The reference warehouse moves. Record the new size on the ticket and write the comparability
  memory: absolute travel numbers across this commit are not comparable (the fourth such break).

## Done when

- Flag-off is byte-identical (the planner runs once at the typed fill, proven); the era sizes
  every bucket from the derived fill; a bucket whose derived headroom is below the declared
  minimum takes the minimum and the record says which; both preflight canaries and an era canary
  through the pool; nine gates green.
