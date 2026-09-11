# Site dock

Label: wayfinder:map

## Destination

The site dock landed on `develop`: both channels' inbound through one yard, one door set and one
receiving crew, put-away drawing on one site pool of putters over segregated volume, flag-off
byte-identical, and the funnel's cells paired — so the inbound campaign runs on the site's own
contention rather than on the per-leaf artefact the independent-warehouse model produces. It stops
BEFORE anything runs: the inbound-optimization map resumes at
[Re-verify the gate under the lead-aware record](../inbound-optimization/issues/26-reverify-the-gate-under-the-lead-aware-record.md).

## Notes

- **Execution override: ON** (the family precedent — both inbound maps and department-calibration
  carry it). Once a ticket's governing decisions close, implementation graduates from fog into
  `task` tickets on this map.
- **Charter — settled during charting (2026-09-11), binding on every ticket:**
  - **The coupling rides the inbound flag.** Flag-off is byte-identical with today's leaf model,
    including its per-leaf put crew. Coupling is what inbound-on turns on.
  - **Put-away is one site pool over segregated volume** (user, 2026-09-11). A putter takes work
    from either channel's queue; a cart or pallet carries one channel's packs only. The crew is
    shared, the volume is not.
  - **The pack is the unit of ownership.** A trailer's LOAD is mixed; PACKING partitions by channel
    before it packs, so every pack leaving the dock has exactly one owning channel. The release
    seam routes by the pack's owner, never by resolving a SKU against a manager's `_originals`
    (`Warehouse/inventory/inventory_reorder.py:442`, the hard break the sizing inventory named).
  - **Put-away shares a DAY BUDGET, not a clock.** Receiving is drain-quantized and lands on the
    site receiving clock at the shared day boundary (already shared: `staffing.py:723-727` refuses
    channels with different batch counts — one batch IS one site day). Put-away's two leaves each
    run their day as today but draw from one pool of crew-seconds, so a channel that finishes early
    releases labour to the other. Within-day interleaving is NOT modelled; event-driven cadence
    stays out of scope, inherited.
  - **`<channel>` survives as a run-tree level.** What changes is the UNIT, not the TREE: one work
    unit now finalizes two channel leaves. Site-scoped artifacts (the yard, its trailers, drains
    and fees) need a home that is not a channel leaf — that is a ticket, not a settled thing.
  - **Arm pairing is the DIAGONAL by rank**, plus the `fifo`/`fifo` pair as reference. The campaign's
    question is "does space-aware inbound beat FIFO"; the arm pair is a CONTROL, not an axis. The
    cross product spends ~4× the wall confounding the inbound comparison with a placement
    interaction nobody asked about. If that interaction turns out to be the interesting thing, it
    is a later effort.
  - **Gain prices a mixed trailer per unit, keyed by owning channel, summed to one trailer score in
    hours.** The objective is already denominated in put + pick hours from the shared cost model
    with no per-channel weighting (inbound-optimization decision 10), so the hours are commensurable
    by construction — but that quietly decides a fulfillment hour and a store hour are worth the
    same to the site, so it is stated and TESTED, never assumed.
  - **Picking stays per channel.** `staffing.py:354-385` — pickers genuinely are per-channel crews;
    only put and receiving are site crews. Coupling the pick floor is a different model.
  - **`run_channel_rollup.py` refuses a coupled run** and keeps its job on inbound-off runs. Its
    validity argument IS channel independence (best plan per channel, summed); under one site dock
    savings are not additive and the premise is void, not merely the plumbing. The whole published
    archive is inbound-off, so refusing costs no history.
- **The double count this effort fixes.** `Optimization/simdriver/workunits.py:366-369` hands EACH
  leaf the whole derived site crew for BOTH put and receiving, so two independent processes field
  the site's labour TWICE — `staffing.py:710-712` says outright that those two crews are site
  totals. Coupling fixes it by construction, and `expected_utilization`'s "single-channel leaves
  undercut ρ" caveat (`staffing.py:663-667`, `equilibrium.py:37-38`) stops being needed for put and
  receiving. **This moves absolute put and travel numbers on every coupled run** — a comparability
  break of the same class as the four already on the record. **And it leaves phase 1 asymmetric:**
  phase 1 is inbound-off, so it ranks placement arms under 2× the site put labour while phase 2
  runs under 1×. That caveat is PUBLISHED with the campaign, not discovered by it.
- **The threshold compromise dissolves, and this map does not own it.**
  `whatif_config.py:70-78`'s fulfillment-calibrated `PHASE2_THRESHOLD_DAYS = 3.0` exists BECAUSE
  two channels bind one crew knob while running as separate simulations. Under a coupled dock the
  threshold is a genuine site property and `gain_gated`'s "fulfillment result, degenerate in store"
  caveat is re-derived — by inbound-optimization
  [Re-verify the gate under the lead-aware record](../inbound-optimization/issues/26-reverify-the-gate-under-the-lead-aware-record.md)
  (25 decision 6), not here.
- **Prior art.** The starting map of seams is
  [`../inbound-optimization/assets/site_dock_sizing.md`](../inbound-optimization/assets/site_dock_sizing.md)
  — a four-layer read-only inventory taken at the resolution that seeded this effort, anchored
  file:line at commit `6eaf30fc`. Its size call: **a redesign of the leaf model, not a handful of
  seams.** Two things are cheap and it says so: transit/dock/packer/timeline are already injection
  (`Inventory_Management.py:254`, `:275`), so one object can serve two managers without touching
  `Warehouse/`; and `staffing.py` already derives site totals, so the coupled run is what the
  record has been describing all along. Re-resolve its line numbers before trusting one.
- **`CONTEXT.md` is ahead of the code, and now covers the scope split.** **Site dock** was already
  a resolved term — *"The one dock both channels' trailers arrive at… contention is a fact of the
  site, never of a channel. A channel run modelling its own inbound alone sees an artefact."*
  **Packing** already carries the channel-partition line the charter needed. 03 added **Channel**
  and **Scope** and amended **Clock**; the decision behind them is
  [ADR-0005](../../docs/adr/0005-inbound-scope-splits-at-the-pack.md). Code identifiers follow at
  build time. Nothing further is owed the glossary by this map unless a ticket coins a term.
- Memories every session should load: `site-dock-is-shared-across-channels`,
  `channel-experiment-independent-warehouses`, `receiving-is-its-own-crew`,
  `one-clock-one-speed-one-config`, `config-knob-has-five-seams`,
  `calendar-span-is-not-work-days` (all in `context/memory/store/`).
- Skills: `grilling` + `domain-modeling` on every HITL ticket; `codebase-design` on the seam
  tickets; `prototype` on the gain ticket.
- Tracker conventions: `docs/agents/issue-tracker.md` (Wayfinding operations).

## Decisions so far

<!-- one line per closed ticket: the gist, then the link for the detail -->

- [Design the site receiving coordinator](issues/01-design-the-site-receiving-coordinator.md): a
  `SiteReceiving` coordinator in `Inbound/receiving.py` holds the one dock, yard and drain record
  and reaches each leaf through two public ports (`plan_lot`, `accept`) — the import boundary makes
  that seam structural, not conventional. **Two findings dissolved the pack-routing question:** a
  `LoadPlan` is already single-SKU (`planned_lots` is per contiguous lot), so a mixed trailer never
  produces a mixed pack; and `regime_of` already answers ownership, so no field is carried and
  `_release_to_stock` — the sizing inventory's "hard break" — does not change at all. Standing yard
  only, refusing loudly otherwise; `check_reorders` untouched as the single-channel composition.

- [Design the coupled work unit](issues/02-design-the-coupled-work-unit.md): the unit is
  `(label, 'coupled', arm_store, arm_ful)` and carries its `group_keys` rather than having them
  sliced off the uid, so one unit finalizes two leaves and a crash finalizes neither;
  `_run_strategy_worker_impl` splits at its existing seam (`:1292`) into a per-channel leaf builder
  called twice under one batch loop, and `_prepare_channel_run` survives unforked as a callee. The
  site crews leave the per-leaf payload for unit scope — **the double count is fixed by deletion**.
  **The charter's "(pair, config, arm-pair)" could not be built:** `config` sits above `channel` and
  the channels use different config sets, so the two leaves share no ancestor below `<pair>/`.

- [Design the site scope in the run tree](issues/03-design-the-site-scope-in-the-run-tree.md): the
  scope splits at the **pack**, not at the dock — packing partitions by channel, so every
  pack-denominated receiving quantity keeps an owning channel and stays in that channel's own DB,
  and `simulation_runs.channel` needs no sentinel and no DDL change. Only the trailer- and
  door-denominated rows are homeless; they go to `<cell>/<pair>/_site/inbound_<arm-pair>.db`, the
  `_dossier` pattern (a literal segment on ordinary artifacts) rather than a new LEVEL, which
  would cost six modules and a verbatim architecture assert. `site` is minted as a fifth
  evaluation scope; **the file carries the scope**, nothing is added to `Col`, because site-ness
  is not a grain. One coupled marker in `run_layout.json` (no contract id moves, and `EvalContext`
  already anchors on that file); pair completeness needs nothing new. Recorded as
  [ADR-0005](../../docs/adr/0005-inbound-scope-splits-at-the-pack.md); **Channel** and **Scope**
  added to `CONTEXT.md` and **Clock** amended. **Three silent seams found and graduated:** the
  reserved-prefix guard exists at one tree depth only, `record_arm` unpacks the now-variable uid
  positionally into a NOT NULL column whose error is swallowed, and an evaluation's scope string
  is validated by nothing.

## Not yet specified

- **The remaining builds** — the coupled half of every design ticket. Two have graduated out
  because they are byte-identical and need no second leaf:
  [Extract the one-leaf receiving coordinator](issues/09-extract-the-one-leaf-coordinator.md)
  (out of 01) and
  [Harden the three positional seams](issues/11-harden-the-positional-seams.md) (out of 03). The
  ADR and the `CONTEXT.md` amendments are **done** (03). What still waits on a second leaf: the
  owner dict, the leaf-accessor refusals, `SITE_PHASES`, the `_site/` artifact declarations and
  their contract bump, and the site evaluation context 07 will need.
- **Within-day put interleaving.** The charter shares a DAY budget, so a putter cannot take the
  earliest-ready cart across channels mid-day. Whether that changes the answer is dim until a
  coupled run shows a day where one channel's put queue actually starves while the other's crew
  sits. The faithful version — both leaves on one time-ordered loop — is a cadence change of the
  same family the inbound map ruled out, so it graduates only with evidence.
- **Where the campaign's staffing record pins a PAIR.** `restock_selection.json` was to pin the
  staffing record and `inbound_policies` refuse under a different one; with diagonal pairing the
  hand-off carries pairs, and whether the pin is per channel or per site is dim until the funnel
  re-shaping closes.

## Out of scope

Inherited unchanged from the closed inbound-optimization map (its Out-of-scope list is this map's
inheritance):

- **Deferral / hold capability** — ruled out by the information horizon.
- **Event-driven decision cadence** — decisions stay drain-quantized.
- **A trailer checkpoint format for mid-flight resume** — uniform-grain refusal-until-clean stands.
- **Loading/dispatch optimization at the ordering site** — upstream of arrival stays as v1 built it.
- **The full multiplicative sweep** — the funnel replaces it by design.

Added here:

- **Coupling the pick floor.** Pickers are genuinely per-channel crews (`staffing.py:354-385`);
  only put and receiving are site crews. A site pick pool is a different model and is not reachable
  from this destination.
- **Running anything.** This map builds the capability and re-shapes the cells. The pilot
  re-verification, the funnel re-sizing, phase 1, selection, phase 2 and publication all belong to
  the inbound-optimization map, which resumes at
  [Re-verify the gate under the lead-aware record](../inbound-optimization/issues/26-reverify-the-gate-under-the-lead-aware-record.md)
  once this one closes.
- **Re-deriving the fee threshold.** The compromise dissolves under a coupled dock, but
  inbound-optimization 26 owns the re-derivation (25 decision 6).
- **The arm-pair interaction as an axis.** The diagonal makes the pair a control; sweeping the
  cross product to ask "does store's best placement change which fulfillment arm wins" is a later
  effort starting from this campaign's result.
