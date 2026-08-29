# Design the space timeline

Type: grilling
Status: resolved

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

## Answer

Resolved 2026-08-29 through two grilling rounds plus a closing elaboration that reopened the
charter's score definition into its own ticket (10). Exploration facts that reshaped the
question before any decision: the rollover carry (`_pending`) is driver-local, unreachable
from `check_reorders`, and EMPTY on default runs (`roll_over_unpicked` off); `check_reorders`
runs BEFORE `Task.from_batch` and the pick sim, so the current batch is released-but-unpicked
at the decision instant; the pick→bin mapping is an exact pure read (`Task.from_batch`'s
drain loop); batch makespan is unknown at decision time; and `_emptied_at` stamps are ALREADY
absolute — the "picker-local" docstrings are stale.

**1. Standing demand = the released batch + the carry.** At `_receive` of batch i, standing
demand is batch i's just-released items plus any rollover carry — one batch deep, injected by
the driver into the timeline BEFORE `check_reorders` (today nothing manager-side ever sees
batch demand). The precomputed `batches[i+1..]` script is the future and stays fenced for the
oracle arm.

**2. Predicted clears: exact and UNTIMED.** The drain loop is extracted from
`Task.from_batch` into one shared pure function (singleton bins before pallet bins, location
order, min(remaining, quantity)); the task builder and the projection both call it, so the
forecast is the sim's own rule by construction (`_rederive_plan` is explicitly NOT the
predictor — its own docstring says its distribution can differ). A bin is predicted-clear
when its projected take equals its quantity. Per the user's correction, NOTHING infers
timing: no makespan estimates, no pick rates against the clock. The only times carried are
facts — actual clear stamps, and the release instant of the demand. The unload-window
horizon gate is DEAD (charter bullet amended; 04's question edited); "how far ahead can
availability reliably be planned" is a future view-arm family, parked in the fog.

**3. Current empties: snapshot, don't mirror.** `_index` stays the single source of truth;
the frozen view copies its per-key lists at ctx-freeze (mid-drain placements swap-remove from
the live lists, so a held reference would violate the frozen contract). Actual clear stamps
are harvested from `_emptied_at` inside `_reclaim_empty_bins` — the one moment the stamps and
the bins meet before both are wiped.

**4. The view: `ctx.space`, a per-drain `SpaceView`.** `DockContext` gains one slot,
`space`, default None — the arrival point the groundwork priority-seams decision reserved;
the policy signature `(candidate, ctx)` is unchanged and fifo keys never read it. Fields:
`empties: dict[BinKey, tuple[Bin, ...]]`; `emptied_at: dict[id(bin), float]` (actual stamps
only; never-occupied bins absent); `predicted: dict[BinKey, tuple[Bin, ...]]` (same keying,
so evaluators walk both tiers uniformly); `released_at: float`; `versions: (demand_v,
reclaim_v, fill_v)`; `frozen_at: float`. NO aggregates — what gets derived from the tiers is
the evaluator's business (04).

**5. Versions: per-event-class counters** — the user's call, so tables can be processed in
parallel from the start without an I/O race. `demand_v` +1 per injection (once per batch);
`reclaim_v` +1 per harvested bin; `fill_v` +1 per placement in `_execute_placement`.
EQUALITY is the only legal operation; magnitudes and cross-class comparisons are meaningless
by contract. The predicted set is a pure function of the other three states, so it carries
no fourth counter. The cache ticket (06) composes keys from this vector and may not add
counters of its own.

**6. Home and hooks.** `SpaceTimeline` in `Inbound/space.py`, constructed by the driver when
`INBOUND_STANDING_YARD` is on, attached to the manager instance (the `BinRecorder.attach`
precedent — rebind, no listener registry; Warehouse imports nothing from Inbound). Four
touchpoints: demand injection (driver, before `check_reorders`); reclaim-harvest
(`_reclaim_empty_bins`); fill (`_execute_placement`); freeze — projection + view build — at
ctx-freeze inside `_receive`. One projection per drain serves every decision in it: no
per-decision rescans. ALWAYS ON with the standing yard (no policy gate, no extra knob): the
cost is one already-paid loop shape per batch, the lockstep proof gets strictly stronger,
and fifo-vs-space arms differ only in the key function.

**7. Neutrality, five obligations.** (1) The degenerate lockstep runs with the timeline ON —
byte-identity vs v1 proves maintenance neutrality end to end. (2) A purity pin: building a
view mutates no manager state and consumes no RNG. (3) An equivalence pin: the extracted
drain rule reproduces `Task.from_batch`'s `bin_pick` on identical state. (4) The
`_emptied_at` AST guard (`test_nothing_reads_the_stamp_to_make_a_decision_yet`) is REPLACED
— its own docstring says to — with a pin naming reclaim-harvest the one legal reader. (5)
The stale "picker-local" docstrings (`inventory_reorder`, `Inventory_Management`,
`fast_pick`) are corrected to absolute in the same change.

**8. Consumption baseline — and the reopened objective.** The decision to stage/unload a
trailer anchors to IMMEDIATELY AVAILABLE bins — the operationally stable signal; `predicted`
rides as data for the planning formulation still to be designed. The closing elaboration
exposed that the charter's placement-quality score may be VACUOUS for trailer ordering
(prime freight wants prime bins, but every SKU benefits from easier picks — every ordering
chases the same spots), and sketched the alternative: an ON-SHELF AVAILABILITY objective
(minimize missed orders — `unmet` / `_shortfall` already count them; per-SKU demand rides
`Order.demand`) and possibly an UNLOAD PLAN — an ordered list of standing trailers
minimizing total future pick work over both tiers. That question graduated as "Define the
inbound objective" (10); the charter bullet now points there.

Graduated: "Define the inbound objective" (10, grilling, frontier) and "Build the space
timeline" (11, task, blocked by 09). Ripples: 04's window clause edited and blocked by 10;
05 blocked by 10 (comment left); comments on 07 and 08 (on-shelf availability candidates).
Glossary: CONTEXT.md gained Space timeline, Space view, Standing demand, Predicted clear.
