# Design the standing-dock mechanics

Type: grilling
Status: open

## Question

Doors become real and the yard stands — design the mechanics that make that true. A trailer
holds its door across drains until fully unloaded; at most `doors` trailers are staged; the
yard-pull fires (drain-quantized) when a door frees. Decide: how partial-unload state is
carried across drains (the pack plan is fixed at arrival — interruption pauses work, never
re-plans); how the receiving crew's per-batch budget spreads across staged trailers; where in
the pinned six-phase `check_reorders` order the pull and unload decisions fire; what single
flag gates the whole standing model; and what flag-off byte-identity means measured against
v1's drain-everything `release()` (`Inbound/transit.py`). Consult `codebase-design`; the dock
intercept lives inside `_admit` (memory: `receiving-is-its-own-crew`), and the phase order is
behaviour, pinned by `Tests/unit/test_reorder_phases.py`.

This is the foundational ticket: the charter's no-deferral, drain-quantized, and
doors-become-real decisions are inputs, not open questions.
