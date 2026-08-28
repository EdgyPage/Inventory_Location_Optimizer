# Design the phased funnel

Type: grilling
Status: open

## Question

Design the two-phase evaluation replacing the multiplicative sweep. Phase 1: inbound-off runs
(matching the historical simulation shape) select the top-k placement/scheduling arms — decide
the selection metric (labor cost? which scope?), k (charter sketch: top 5 of the ~34/36-arm
suite), and how the selection is recorded so phase 2 can consume it. Phase 2: inbound-on runs
sweep top-k × the inbound policy arms — decide how those cells are generated: is the cell
generation machinery redesigned to be phase-aware (a selection stage between phases), or are
the two phases simply two runs with a hand-carried arm list? Constraints from the charter:
byte-identical determinism throughout, resume at one uniform grain across the operation
(refusal-until-clean), and the two simulation levels (inbound on / inbound off) must coexist
in one config surface. The `Cell` NamedTuple already has room for another axis (memory:
`putaway-seams-for-inbound`); `analyze_run`'s cross-cell what-if writers are the phase-1
reading surface.
