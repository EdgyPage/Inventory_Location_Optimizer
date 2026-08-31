# Build the futuresight window feed

Type: task
Status: resolved
Blocked by: 05, 14

## Question

Build decision 6 of "Define the inbound objective" (10) — the plumbing only; the policy
entries that read it are the arm roster's (05). The pieces:

- A `SpaceView` slot of its own, never merged into standing demand: `inject_demand`
  REPLACES `demand` by charter and `predicted` projects from exactly that field, so a
  merged window would silently redefine "Predicted clear" and break the one-batch-deep pin
  (`Tests/unit/test_space_timeline.py`). Shape: the flat per-batch `{sku: qty}` dicts of
  `batches[i+1 .. i+w]`, copied — never the shared pickle objects (the shared-pickle
  mutation hazard is documented at the injection site in the driver).
- Fed at the existing standing-demand injection site in `strategy_runner` — one slice of
  `batches`, no new import edge (Inbound already receives plain dicts).
- No fourth version counter: the window changes in the same event that bumps `demand_v` and
  is a pure function of the batch index, so it shares `demand_v` — the three-counter
  contract from the space-timeline design (03 §5) stands, and the cache ticket (06)
  composes keys unchanged.
- Clamp at the end of the script; an empty window is an empty slot, not an error.
- The script is REQUIRED: when `batches` is None (fingerprint miss → inline sampling), a
  run with any futuresight arm refuses loudly at startup — never silent inline window
  sampling (refusal-until-clean, the effort's own precedent).
- The window knob (name, per-arm declaration, `w` denomination in batches) lands with the
  arm roster (05) — build to whatever it names; blocked on it for exactly that reason.

## Comments

2026-08-29, from resolving "Name the policy arms and their knobs" (05): the knob is
`INBOUND_FUTURESIGHT_BATCHES` — int, `'all'` = the oracle w=∞ (a string sentinel that
survives a run spec), default None = inert; the arm refuses loudly when the knob is unset
or the precomputed script is missing. Scope change: this ticket now ALSO builds the
`futuresight` registry entry itself (`gain_forecast` over the window slot, both
registries), so it is additionally blocked by "Build the gain evaluator and the gain-plan
arms" (14), which supplies the evaluator the entry calls. The feed half is unchanged.

2026-08-29, from resolving "Draw the cache-sharing boundary" (06): the window feed gets
NO artifact — it is cell-shared by INHERITANCE from `_batches_*.pkl` (already one file per
pair, re-opened per worker), so the feed is read-ahead over the worker's in-memory batch
list, a pure function of (script, batch index) riding `demand_v`. Do not materialize a
feed file, do not add a sharing mechanism; the ticket's copy-the-dicts rule stands (copies
guard the shared pickle objects, they are not a cache).

## Answer

Built 2026-08-30, commit `dd44d8a`. Both halves — the feed the ticket body specifies and
the `futuresight` entry the 05 comment moved here — are code, tests green (unit tier
1424).

**The feed, exactly as specified.** `SpaceView`/`SpaceTimeline` gain a `window` slot:
`inject_demand(..., window=None)` sets it in the same event that bumps `demand_v` (no
fourth counter; the 03 three-counter contract untouched), replaced wholesale per batch,
carried through `freeze` by reference (immutable tuple of driver-owned copies), and the
predicted-clear projection never reads it — a merged-window regression is pinned by a
test whose window quantity would clear a whole pallet if merged. None = no feed (every
lawful arm); `()` = a futuresight run at the end of its script, legal by construction.
The driver slices `batches[i+1 .. i+w]` at the standing-demand injection site via
`_futuresight_window` (module-level, so the clamp and membership are pinned directly):
clamped at `n_batches` — never `len(batches)` — and each `.items` dict COPIED off the
shared pickle, with the mutation hazard documented on the helper. The window is built
only when a futuresight policy is actually named — no unconsumed infra.

**The entry, and THE ONE DESIGNED DECISION.** The record pinned the slot's SHAPE (raw
per-batch demand dicts) but never the consumption formula. Built from decision 6's own
words — "its edge over the static rates is sampling-noise knowledge (which SKUs land,
exact counts)" — the window stands exactly where the static rates stand, the PRICING:
`futuresight` = `gain_forecast` (same greedy, same deferral pool — predicted stays one
batch deep; the window never projects bins, which the slot's raw-dict shape forecloses
anyway since projection needs freeze-time manager access) with `_pair_cost` reading
realized window demand: a SKU absent from the window pays put travel only; a present one
prices at its realized per-event draw (window total / window events) with visits capped
at the event count — the cap is what makes w=∞ honestly the oracle (a unit cannot be
visited more often than demand events exist). The placement MACHINERY stays static
(faithful-to-arm, 10 decision 5: the real pool cannot see the future, so the merge
priority and pool precedence keep the arm's own rates). Rates-match invariance is the
governing pin: a window whose realized rates equal the static ones reproduces the
forecast plan exactly, both through `plan_order` and through the registry entry itself.
Registered in BOTH standing registries and added to `GAIN_POLICIES` (the entry needs the
driver-injected bundle like any gain arm).

**The knob and the refusals.** `INBOUND_FUTURESIGHT_BATCHES` rides settings → CONFIG →
`inbound_spec()` → the worker payload as part of the one `inbound` record; 0 survives
the explicit None tests at every seam (a legal blind pole); `'all'` survives as the
string sentinel; negative, fractional, bool and garbage-string values raise the
KNOB-NAMED error at spec build. Refusal-until-clean, all three layers: the entry raises
on a None window slot (a missing feed must never silently rank as `gain_forecast`); the
worker's startup gate (`_futuresight_window_w`) raises when the arm is named with the
knob unset or with `batches` None (inline sampling — never a silently different future);
and — a code-reviewer find, fixed family-wide — `inbound_spec()` now refuses ANY
non-fifo yard/dock policy without the standing yard, closing the hole where a
futuresight-configured run would have completed as v1 fifo under the arm's name.

**Reviews.** code-reviewer: no critical findings; folded the standing-yard spec refusal,
the `plan_order` `_ev`+`window_rates` latch (a hook evaluator would silently drop the
window), the bool/knob-named-error normalization, and the stale `priorities.py`
registration docstring. test-reviewer: determinism proven over three runs including
reversed file order; folded the extracted-slice pins (exact membership, both clamps,
the copy guard), the w=0 gate pin, `match=` on the knob raises, and the
entry-through-the-seam equivalence test. Not taken, recorded: window pricing never meets
the pool adapter in a test (`_pair_cost` is the shared pricing point; revisit if a
rank-arm futuresight run is ever swept).

**Flagged for the funnel (08).** Under `'all'` on a deep run the feed is O(n²·|batch|)
over the run (per-injection copies plus per-drain re-aggregation), deliberately unmitigated
here (06: no cache machinery; the copies are not a cache) — and the cost lands inside the
`t_reord` section, so the futuresight arm's deep-tier reorder seconds will read inflated,
worst at run start. Take a bench number before sweeping `'all'` at depth, and read that
arm's `t_*` with this in mind.
