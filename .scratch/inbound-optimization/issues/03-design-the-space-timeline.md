# Design the space timeline

Type: grilling
Status: open

## Question

Design the event-stamped space structure both dock decisions read — the charter's "more data
against the global clock." It extends the dormant `_emptied_at` substrate into an
incrementally-maintained timeline: current empty bins plus bins predicted to clear, each with
an absolute-clock stamp. Decide: what the predicted-clear entries are computed from (STANDING
DEMAND only — released-but-unpicked batches, per charter; how a queued pick maps to a
bin-clear prediction and stamp); the maintenance rules (picks empty bins, puts fill them —
event-driven updates, no per-decision rescans); the version-stamp scheme that cache
invalidation keys on; and the frozen named views `DockContext` exposes to yard/dock policy
keys (the arrival point reserved by the groundwork priority-seams decision — a named view on
`ctx`, no signature change). Pure data: with both policies `'fifo'` the timeline must change
no behavior.

Consult `codebase-design`. Anchors: `_emptied_at[id(bin)]` (memory:
`putaway-seams-for-inbound`), `Inbound/priorities.py` (`DockContext`, the pure-key contract).
