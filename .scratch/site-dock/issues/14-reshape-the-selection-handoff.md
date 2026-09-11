# Re-shape the selection hand-off

Type: task
Status: open
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
