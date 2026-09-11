# Re-shape the selection hand-off

Type: task
Status: resolved
Blocked by: 06

AFK. The selection-side half of
[Re-shape the funnel for arm pairs](06-reshape-the-funnel-for-arm-pairs.md), graduated out because
it needs **no second leaf**: every change here is in `Optimization/run_restock_selection.py`, which
reads a finished phase-1 run and writes one JSON. It is buildable and testable today, before the
coupled work unit exists. The SPEC side of 06 — `PHASE2_PAIRS`, the derived `CHANNEL_RESTOCKS`, the
pair-shaped refusal in `_run_whatif_matrix` — stays in the map's fog, because a pair means nothing
until there is a unit builder to consume one.

## Question

Build the four selection-side decisions of 06. Read its `## Answer` for the reasoning; this ticket
is the work, not the decision.

1. **The `pairs` field** (06 section 1). Zip the two channels' **ordered `chosen` lists** into
   `[(store_rule, fulfillment_rule)]`, rank-aligned. Note that `_choose` currently returns
   `'arms': sorted(set(arms))` (`:246`) — the sort destroys the only thing a diagonal reads, so the
   pairing must be built from `chosen`, and `arms` keeps its existing per-channel meaning for the
   leaf projections. Ragged rankings zip to the common length, log loudly, and stamp
   `pairs_complete: false` with the reason; `select()` still writes, because the ranking is what a
   human reads to decide between re-running phase 1 and accepting a shorter campaign.

2. **The `('fifo', 'fifo')` rider**, appended as a pair if absent — outside k and outside the cap,
   exactly as the scalar rider is today (`:232-247`).

3. **The union extension cap** (06 section 8). The cap becomes a bound on the **union of distinct
   unfaithful families across both channels**, not a per-channel bound: extending
   `_gain_bundle_for` is work per family, so a per-channel cap of 3 can commit six. Channels are
   consumed in the artifact's existing deterministic order (`sorted(by_channel.items())` →
   fulfillment, then store) and **that order is recorded in the artifact**, because it is part of
   the decision rather than an implementation detail. Also report `needs_bundle_extension` per
   **pair** (a pair is runnable only if both members are faithful), alongside the existing
   per-rule reporting.

4. **The coupled-root guard** (06 section 7). `select()` refuses a run root whose `run_layout.json`
   carries `coupled: true` — phase 1 is uncoupled by definition, and pointed at a coupled root the
   selector would rank coupled leaves and emit a hand-off artifact indistinguishable from a real
   one. It already loads `read_run_layout(base_dir)` at `:283`. Write it defensively
   (`layout.get('coupled')` is falsy on every run that exists today), so it is inert until
   [Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md)'s marker
   lands and starts biting the moment it does.

5. **The `put_regime` stamp** (06 section 3). Record `put_regime: 'per-leaf'` on the artifact. There
   is no runtime gate to pair it with — nothing in the codebase reads two run roots — so this stamp
   and the published caveat are the whole of how the phase-1/phase-2 asymmetry is carried.

**Naming discipline, and it will bite:** `pair` already means the *inventory profile* throughout the
harness (`run.pairs` in this very artifact, `<cell>/<pair>/`, `_derive_staffing_for_pair`). Every
new identifier and comment says **arm pair** or **rule pair**, never a bare `pair`.

Tests belong in `Tests/unit/test_restock_selection.py`, which already exists and already asserts the
rule-not-arm unit. Real `assert`s (CLAUDE.md section 3); the ragged-zip and union-cap paths each
need a case that can actually fail.

## Answer

**BUILT and live on `develop`** (`c2761d3d`, arch layer `fed911c2`). All five items landed inside
`Optimization/run_restock_selection.py`; `restock_selection.json` gains `rule_pairs`, three
extension fields, `baseline_rule_pair` and the `put_regime` stamp, and `select()` refuses a
coupled root. Ten new tests in `Tests/unit/test_restock_selection.py`, nine mutations planted,
nine caught.

### What is in

1. **The rank diagonal.** `_rule_pairs(channels, log)` zips the two channels' ordered `chosen`
   lists, store first, into `[[store_rule, fulfillment_rule], ...]`. Built from `chosen` and never
   from `arms` — the mutation that swaps them is caught by a test whose two channels are given
   *opposite* rankings, so the diagonal and the alphabetical zip cannot agree by accident.
   `arms` keeps its per-channel meaning untouched.
2. **The rider.** `('fifo', 'fifo')` appended if absent, outside k and outside the cap.
3. **The union cap.** `_choose` takes a mutable `committed` list threaded through every channel:
   a family an earlier channel already committed is free, a new one is charged, and the cap
   bounds the union. `extension_channel_order` (`['fulfillment', 'store']`) and
   `extension_union` are recorded, with `extension_cap_scope` saying in prose why the bound is a
   union. `needs_bundle_extension` is reported per rule pair as well as per rule.
4. **The coupled-root guard.** `SystemExit` naming the run and saying what it would have
   produced. Inert on every run that exists today, and a test asserts that too — a guard that
   refused the whole existing archive would be discovered by a person, not a test.
5. **The stamp.** `put_regime: 'per-leaf'` plus a `put_regime_note` that says RANKS are no more
   invariant than hours across the phase boundary, because a reader who takes the stamp as
   "hours only" would go on comparing ranks. The test asserts that word is present.

### Three deviations under force

- **The field is `rule_pairs`, not `pairs`** — and `pairs_complete` is `rule_pairs.complete`.
  The naming discipline the ticket flagged bites in exactly this document: `run.pairs` here
  already means the inventory profiles, so a sibling `pairs` meaning rule pairs would put two
  senses of the word in one JSON object. Sub-keys: `order`, `chosen`, `complete`,
  `incomplete_reason`, `rider`, `needs_bundle_extension`.
- **A one-channel run produces NO pairs and fabricates no rider.** The ticket's rule "appended as
  a pair if absent" is well-posed only where a diagonal exists; applied to a store-only run it
  yields a one-entry pair list holding nothing but a manufactured `('fifo', 'fifo')`, which reads
  as a very short campaign rather than as the single-channel run it came from. `complete: false`
  with a reason naming the absent channel instead.
- **The guard moved to the top of `select()`.** The ticket pointed at the existing
  `read_run_layout` call, which sits *after* the whole ranking; refusing there would rank every
  coupled leaf first and decline afterwards. The load is hoisted, and the test asserts no
  artifact exists on the refused root — a half-written hand-off is the failure the guard exists
  to prevent.

### Two findings

- **This module is a run-tree SHAPE SOURCE** (`contract.SHAPE_SOURCES` lists it), so an edit that
  adds a JSON *field* and moves no path still moves the source fingerprint and costs a full
  preflight canary pair. It was paid twice here: once for the body, and again for a one-line
  docstring headline edit made afterwards for the files-catalog purpose. Batch every edit to a
  shape source before running preflight.
- **`context/artifacts.yml` records this artifact's top-level `fields:` and nothing verifies it
  against the writer.** `verify_context.py` passed green with the list stale by seven keys.
  Updated by hand; the same silence covers all 36 artifacts.

Gates: 2037 unit (25 in this file), all nine verifiers, both preflight canaries `tree shape
UNCHANGED`.
