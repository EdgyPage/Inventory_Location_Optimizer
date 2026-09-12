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
  - ~~**The coupling rides the inbound flag.**~~ **SUPERSEDED — coupling is DECLARED.** The
    byte-identical half stands: flag-off is today's leaf model, including its per-leaf put crew.
    But the trigger cannot be the inbound flag, because
    [Re-shape the funnel for arm pairs](issues/06-reshape-the-funnel-for-arm-pairs.md) couples
    every cell **including its inbound-OFF pole** — so a coupled run with no trailers at all is
    exactly what the campaign runs, and no flag implies coupling. Built as `--couple-channels`
    with `run_layout.json`'s `coupled` as the run-tree marker
    ([Build the coupled work unit](issues/18-build-the-coupled-work-unit.md)).
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
  break of the same class as the four already on the record. **It does not move them YET:**
  [Build the coupled work unit](issues/18-build-the-coupled-work-unit.md) deleted the per-leaf
  crew from the payload, which is byte-identical, and the crews are still FIELDED per leaf until
  04's pool lands. The break therefore has a date and a test — it arrives with
  `Inbound/putaway_pool.py`, and `test_a_coupled_unit_matches_the_two_units_it_replaces` fails on
  the commit that causes it, naming the leaf whose labour moved. **And it leaves phase 1 asymmetric:**
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
- **The route (charted in full, 2026-09-11).** Every DECISION is closed; what remains is the
  execution the override carries. Seven build tickets, and three of them are independent:

  ```
  20 site space view ──▶ 21 coupled receiving coordinator ─┬─▶ 24 site analysis stage ──▶ 25 site crew checks
      [DONE]                    [DONE]                     └─▶ 26 composite gain bundle
  22 coupled resume reconciler  [DONE]   (independent)
  23 funnel spec for arm pairs  [DONE]   (independent)
  27 the site dock's unload price  (grilling, OPEN -- graduated by 21; 24 needs it)
  ```

  **20, 22 and 23 landed together on 2026-09-11**, run in parallel — which is also how the three
  independent tickets were meant to be used, and **21 followed the same day**. What is left is
  24 -> 25, plus 26 off 21, and the one DECISION 21 graduated (27, which 24 needs before it can
  build the site dock). The map closes when 25 and 26 are in; the inbound-optimization
  map then resumes at
  [Re-verify the gate under the lead-aware record](../inbound-optimization/issues/26-reverify-the-gate-under-the-lead-aware-record.md).
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
- [Design the site put-away pool](issues/04-design-the-site-put-away-pool.md): the pool IS a shared
  `list[float]` — both leaves' queues bind the same clocks, so `crew_clock.charge` books every put
  to whoever is free earliest across both channels and segregation survives in the queues, not the
  people. One crew at unit scope with `first_uid = max(k_pickers)` (a uid gap in the smaller leaf,
  in exchange for a putter meaning the same person in both DBs). The day is split by the two
  recorded `expected_utilization` values — no new record field — then a **residue pass** makes
  "finishes early releases labour" true in both directions; without it fulfillment absorbs every
  cut as a pure artefact of loop order. `Inbound/putaway_pool.py` owns the rule, the list and the
  once-per-day reset (01's two ports become three); the boundaries leave `Inbound/` the only
  package that may sit above two managers. Two prices over one crew, stated and TESTED against the
  `s_put` ratio. The band reads ONE site number and retires the "single-channel leaves undercut
  rho" caveat for put. Two loud refusals: no working-day grid, and `PUT_QUEUE_SPLIT` (two answers
  to the same question). **Amends 02: `put_clock` becomes site-wide**, based at the site day start
  — a shared list cannot carry two epochs. The source stamp and the carryover key both dissolved.

- [Design the composite gain bundle](issues/05-design-the-composite-gain-bundle.md): a
  `SiteGainBundle` of two whole `GainBundle`s, dispatched by a one-line owner cursor in `_params`
  — **the seam already exists**: `place_load` groups by BinKey and BinKey DETERMINES regime
  (`inventory_common.py:42-43`), so the charter's "per-unit, keyed by owning channel" needs no
  per-unit loop, and `_gain_bundle_for` is called twice UNCHANGED, which makes faithful-to-arm
  structural rather than argued. Half the bundle was already site-wide (`put_speed` is one site
  CONFIG; `wp_of` already dispatches per regime). **The prototype killed its own first draft:**
  wrapping `place_load`'s group loop from outside silently breaks the `avail_cache` continuity that
  lets spill resume where consumption left off — the cursor goes INSIDE. One path, via a one-owner
  wrapper, so flag-off is the same instance and no `if coupled:` sits in the pricing hot path.
  **Commensurability is well-posed because a mixed trailer decomposes EXACTLY by owner** (bins are
  BinKey-partitioned, so the two owners never contend); the recovered exchange rate is a = b = 1,
  and the test carries a SABOTAGE part that fails on any per-channel weight. Sub-questions 4 and 5
  routed to 08 and 06 rather than pre-empted.

- [Re-shape the funnel for arm pairs](issues/06-reshape-the-funnel-for-arm-pairs.md): a phase-2
  cell is an ordered list of RULE pairs (`PHASE2_PAIRS`), rank-aligned and store-first, with
  `CHANNEL_RESTOCKS` **derived** from it rather than authored beside it — a pair list makes a
  ragged hand-off unrepresentable, where two zipped lists carry the silent-truncation failure all
  the way to the launcher. The diagonal extends to `Strategy.stock_mode` (`uniform`/`policy`, a
  clean 17/17 partition of every rule), so a rule pair is **two** arm pairs and never four, and
  phase 2's arm count is unchanged. **Every cell couples, `inb_off` included:** the trade is
  cross-phase comparability for within-matrix validity, and the matrix's own deltas are what the
  campaign publishes — the anchor is restated as the inbound-OFF pole INSIDE the coupled model,
  which keeps 03's run-root marker honest because no cell opts out. The extension cap counts the
  **union** of unfaithful families (extension work is per family, so a per-channel cap of 3 can
  commit six), at the cost of a declared alphabetical channel order. The staffing pin is per
  **site** — `_derive_staffing_for_pair` was always per-pair, so there is no per-channel
  derivation to pin, and coupling changes only how the crews are FIELDED, which is what makes the
  pin work unchanged across the boundary. **Three findings:** `sorted(set(arms))` destroys the
  rank order a diagonal reads, so the artifact could not carry a pairing at all; **nothing in the
  codebase reads two run roots**, so the cross-phase caveat is a stamp with no join to gate and
  the one reachable guard is `select()` refusing a coupled root; and work units are built PER
  CELL, which made cell-level coupling free and turned the anchor into a real choice rather than
  a plumbing detail.

- [Re-scope the analysis surfaces to the site](issues/07-rescope-the-analysis-surfaces.md): **two
  of the ticket's four premises were false, and the false ones were the expensive ones.**
  `derive_views` never reads scope (`quantities.py:803-824`), so a site quantity cannot render an
  undeclared view and no new quantity is declared here; and the receiving self-check §2 asks to
  preserve **was deleted** — `reference.recv_exact_check` is gone, `unload_price_for` has zero hits
  repo-wide, and its loader survives with zero callers. What is real is the scope machinery: a
  third flat-pool stage in `run_analysis`, one job per `(pair, arm-pair)`, on a `SiteContext` that
  **populates the same `_by_key` shape** — brokers key on `db_path`/`run_id` and nothing else, so
  every existing broker works unchanged and site-ness lives in what the keys point at. Door
  utilization takes the SITE calendar span, receiver busy both leaves' seconds over distinct
  `work_day`, per-channel shares printed beside — never instead of — the site number. The
  equilibrium report assembles the site clause itself (`equilibrium.py` states "no run tree" and
  means it); **site bands, leaves report**. The rollup refuses with a `ValueError`, not
  `SystemExit`, and `analyze_run` skips it outright. The `yard` family's scope becomes a third
  value, which moves `schema_id` and touches four test ties. **Three findings:** a bare
  `scope='site'` is **silently namespaced as `config`** by `requests.py:601` and prepares no output
  directory; `SystemExit` from the rollup would have **aborted `analyze_run` mid-run**, because
  `_step` catches `Exception` and that is not one; and `yard_overage_total` rides in **every** arm's
  series doc, so a headline evaluation carries a site quantity today.

- [Design the site space view](issues/08-design-the-site-space-view.md): the coordinator **composes
  two frozen views** and `freeze` is not touched — `Inbound/space.py` keeps its defining property
  (it imports nothing from Warehouse), the purity pin keeps its subject, and site-ness lives in a
  pure composer over frozen data. **The field census is nearly inverted:** only four of seven
  fields are read in production, and `versions`, `released_at` and `emptied_at` are read by
  **nothing** outside tests. The two tiers compose differently — `empties` copies the
  WHOLE-geometry `_index` and must be regime-filtered, while `predicted` is projected from the
  leaf's own demand and is already clean, so filtering all three would be three times the surface
  for one defect. `versions` composes **element-wise**, `((d,d'),(r,r'),(f,f'))`, which keeps 06's
  sub-vector cache keys positionally addressable by event class; a SUM is illegal and lossy in the
  exact failing direction. One site `frozen_at`, per-regime `released_at` never averaged, and the
  `window` rule recorded while the build REFUSES (no lawful arm sets one). Two injections, one per
  leaf. **The finding:** `BinKey` is a plain tuple, so `regime_of(key)` returns `'store'` for every
  key, silently — verified both ways; the filter tags by the leaf the coordinator just froze and
  asserts with `regime_of(bin)` on the value, never on the key.

- [Extract the one-leaf receiving coordinator](issues/09-extract-the-one-leaf-coordinator.md):
  **BUILT and live on `develop`.** `Inbound/receiving.py` owns the standing drain, the manager
  keeps only `plan_lot` and `accept`, and the driver binds the coordinator where it already builds
  the `Dock` and the `YardTransit` — one implementation, exercised by production. **The ticket
  contained a decision after all:** its items 2 and 3 pulled against each other, because
  `Warehouse <-> Inbound` is forbidden both ways, so leaving the driver untouched would have meant
  the drain existing TWICE (the two unload modes cannot be imported back into the manager). Wiring
  it now makes `test_standing_yard_e2e.py:193` — row-for-row over every table — the byte-identity
  proof instead of one hand-written test. Both port signatures deviate from 01 under force:
  `plan_lot` must return `(plans, items)` because a plan holds several units and the grouping is
  not recoverable, and `accept` takes `dur` alone because `t0`/`w` are the dock's row. The yard row
  is RETURNED, not recorded, which left `drain_yard_drains` and its five call sites untouched.
  **Two findings:** the test sweep was five `_manager` helpers, not the ~56 sites a raw count of
  `YardTransit(` suggested — counting constructor calls is not counting call sites; and the
  behavioural equivalence test **passed on mutated code** (zero-lead scenario, so phase order moved
  nothing), so the order is now pinned as a recorded call SEQUENCE, and both failure modes were
  re-checked by mutation. Gates: 2010 unit, 4 e2e, arch + site + context + guards + memory all OK.

- [Design the coupled unit's resume guard](issues/10-design-the-coupled-resume-guard.md): **both
  leaves or neither, and a torn pair REPAIRS rather than refuses** — no new marker, because
  `_finalize_config_run` runs only when `members <= done_uids`, so two finalized leaves cannot
  exist without a successful unit (a pair-level marker would tear in its own right, and re-run a
  FINISHED pair). The replay is **forced, not chosen**: one batch loop over two leaves across a
  shared dock, put clock and `recv_clock` means leaf B cannot step without leaf A, so the orphaned
  complete leaf is reset exactly as a partial arm is — the existing strategy-granularity reset,
  bit-identical, widened from an arm to a unit, at `log.warning`. The reset extends to the unit's
  **third output**, `<pair>/_site/inbound_<arm-pair>.db`, which `reset_strategy_db` does not know;
  it is the only worker-written site artifact. `coupled` becomes an **independent** third reason in
  the batch-grain refusal, because the two that fire today are contingent (`receiving` evaporates
  below a derived crew of 1, `roll_over` is a work-day knob) and neither is a statement about
  coupling — **which answers sub-question 4 by REFUSAL, not assertion**: two leaves could lawfully
  restart at different batches, and one batch is one site day, so no downstream code should assert
  about a state that must not be reachable. `_reconcile_coupled_unit` in `workunits.py`,
  parent-side before any worker opens a file. **Four findings:** `find_run` resolves
  `ORDER BY run_id LIMIT 1` — the **oldest** run, so a DB that acquires a second one answers every
  filtered query from the abandoned run and doubles every unfiltered aggregate, with no symptom; a
  one-leaf torn window **exists today** on flag-off runs, because the completeness marker is
  written LAST; a **fifth** positional seam (`supervisor.py:108-110`'s `expected_pick` write-back)
  turns a SUCCEEDED coupled unit into a logged `strategy FAILED`, amended into 11; and
  `_finalize_config_run`'s additive-run merge describes a path `skip_completed = resume` blocks.
  No glossary term and no ADR, and the ticket says why.

- [Harden the three positional seams](issues/11-harden-the-positional-seams.md): **BUILT and live
  on `develop`** — three commits, one per REASON rather than per seam: the two amendments made
  seams 2 and 5 share two files, and 4 is worthless apart from 3. `RESERVED_PREFIX` is now READ by
  every walker and applied at the pair, config AND channel depths; a work unit STATES its identity
  (`workunits._stamp_identity` stamps `group_keys` + `arm_key`) so neither `record_arm` nor the
  `expected_pick` attach slices a uid; and an evaluation's scope is validated against
  `registry.SCOPES` at decoration AND given a namespace by a map TOTAL over those scopes, with
  `site` minted as the fifth. **One decision the ticket did not contain:** a multi-leaf unit's
  single `expected_pick` is REFUSED with a warning rather than copied onto both arms — a new
  silent wrong answer is worse than an unattached value, and the shape stays 02's to settle.
  **The fourth spelling of "no channel" was three sites, not one** — the runtime LOOKUP was as
  wrong as the UPDATE, so a store arm's identity gate read a row it could never find; the rule now
  lives on `ChannelRun.channel_key`, beside `group_key`. Byte-identity proven by the preflight
  canaries (two full runs through the real pool, `tree shape UNCHANGED`), not only by tests; every
  new guard mutation-checked. **Two findings:** the architecture layer was stale BEFORE this work
  — 09 never ran the chain, so the derived commit syncs `8a5b4f42` too and the marker moves to
  `4066071a`; and `--catalog-merge` seeds a new entry's `purpose` from a docstring FRAGMENT, which
  is not a `purpose: TODO` and so escapes CLAUDE.md §1's fill step entirely.

- [Seat the put-pool injection seams](issues/12-seat-the-put-pool-seams.md): **BUILT and
  live on `develop`** — `bind_crew` takes a pre-built `clocks` list, held on the MANAGER
  (`_put_pool_clocks`) because the queue set can be replaced after the bind and a rebind
  that minted fresh clocks would drop the pool with nothing raising; `drain_putaway_records`
  can hand the records over without ending the day, with `_put_clock` moving with the flag
  rather than beside it; and `_stock`'s tail loop becomes a public `count_put_cut(deadline)`.
  **Two deviations under force:** the ticket as written produces a HALF seam — a public
  `count_put_cut` with no way to stop `_stock` charging cannot be called *instead* — so
  `_stock` gains `charge_cut`, and a test asserts the inflation it prevents; and a queue
  naming its own `spec.crew` REFUSES an injected pool, as does a size contradicting the
  list, because a half-applied pool is neither model and no row tells them apart. **Three
  findings:** 04's "hand both managers' queues the same list" is well-posed only while
  `put_queue_split` is off — and it always is, because `refuse_unpriceable_put` already
  refuses the split for the era derivation and every coupled run derives, so the new
  refusal is that same incompatibility stated a third time rather than an open decision;
  `_put_clocks` was already the name of the defect this file fixed, three lines above the
  new guard; and `PutQueueSet.snapshot()` is DESTRUCTIVE, draining every counter it reads.
  Byte-identity measured, not argued — both preflight canaries against a `git archive HEAD`
  copy, `work_events` (668 put rows) and `put_queue_state` row-for-row to zero; six guard
  mutations, six caught.

- [Seat the one-owner bundle indirection](issues/13-seat-the-one-owner-bundle-indirection.md):
  **BUILT and live on `develop`** — `_Evaluator` holds a PROVIDER and resolves
  `for_key(BinKey)` through a cursor `_params` advances once per group; the driver wraps its
  one bundle in `OneOwnerBundle`, which answers every key with that same instance, and
  `_gain_bundle_for` is untouched. A bare bundle is REFUSED rather than sniffed for, so the
  one path 05 decision 3 asked for has nothing to rot into. **One decision the ticket did not
  contain:** `gain_gated` reads its two days-denominated knobs off `ctx.gain` ITSELF, which is
  now the provider — so the provider carries them, because the gate composes hours and days
  ABOVE any one owner and must not pick an arbitrary owner's copy; **the composite therefore
  owes a loud refusal when two owners' copies disagree**, stated here so 05's build does not
  rediscover it. **The finding:** a mutation moving the cursor INSIDE the `_wp` memo
  **survived** a single-call test — the memo is cold on the first `place_load`, so the defect
  only shows on a WARM evaluator, which is what `plan_order` actually uses. Byte-identity
  measured, not argued: 6 scenarios x 5 adapters x both deferral modes against a
  `git archive HEAD` copy, **4,675 lines and one differing** (the flag naming which path ran),
  against 1,506 under a mutant resolution; both preflight canaries `tree shape UNCHANGED`.
  Gates: 2027 unit, 31 e2e, all nine verifiers. Five guard mutations, five caught.

- [Re-shape the selection hand-off](issues/14-reshape-the-selection-handoff.md): **BUILT and
  live on `develop`** — `restock_selection.json` carries the rank diagonal as `rule_pairs`,
  zipped from the two channels' ORDERED `chosen` lists (never from `arms`, which is
  `sorted(set(...))`: a pairing built from it is alphabetical, the right length, and wrong in a
  way nothing downstream detects — the test gives the two channels OPPOSITE rankings so the two
  cannot agree by accident). The cap now bounds the UNION of unfaithful families through a
  `committed` list threaded across channels, with the consumption order recorded because an
  alphabetical tiebreak between two incomparable hour scales is a decision; `select()` refuses a
  `coupled: true` root; and `put_regime: 'per-leaf'` is stamped with a note saying RANKS are no
  more invariant than hours. **Three deviations under force:** the field is `rule_pairs`, not
  `pairs` — the naming trap bites in this very document, where `run.pairs` already means the
  inventory profiles; a one-channel run produces NO pairs rather than a lone manufactured rider,
  which would read as a very short campaign; and the guard moved to the TOP of `select()`,
  because the existing `read_run_layout` call sits after the whole ranking and refusing there
  would rank every coupled leaf first. **Two findings:** this module is a run-tree SHAPE SOURCE,
  so adding a JSON *field* that moves no path still costs a full preflight canary pair (paid
  twice, the second time for a one-line docstring edit); and `context/artifacts.yml` records each
  artifact's top-level `fields:` with **nothing verifying it against the writer** — the context
  gate passed green with this one stale by seven keys. Gates: 2037 unit, all nine verifiers,
  both canaries `tree shape UNCHANGED`; nine guard mutations, nine caught.

- [Reconcile the site crews' work stream](issues/15-reconcile-the-site-crews-work-stream.md):
  **a uid is not an identity — `(role, uid)` is**, because 04 preserves `actor_uid == picker_id`
  (so the two channels' picker 3 are different people) while the putter's uid is deliberately
  the same person in both DBs. Check 3 is therefore untouched per-leaf and gains a pair-scope
  companion in two clauses: no uid carries a site role in one leaf and a per-channel role in the
  other (04's defect, invisible today), and every site-role uid sits above both leaves' pick
  blocks — a FLOOR, never set equality, because an idle putter writes no rows. **No roster
  artifact and no magic uid range**: which roles are site roles is a constant in the scope the
  check runs in. Checks 1 and 2 stay per-leaf, but **confirming that found the tautology**: both
  surfaces delegate to `self._dock` (`:1199`, `:1208`), so under one coordinator-held dock a leaf
  handed the whole drain would agree with itself and PASS while owning the other channel's entire
  receiving labour — so the build owes the precondition that each leaf's `batch_stats` scalars
  come from its own `_recv_seconds`, plus a site-total closure check against the coordinator's
  independently-accrued total. **The exact re-pricing check returns for far less than the memory
  implies:** the price collapses to `duration - qty × sku_scores.handle_var == C`, one constant
  from the sim DB alone, so `load_receive_events` and `receive_event_frame` get the caller they
  were written for instead of the deletion `hand-run-test-tiers-rot-silently` would have earned
  them; repack rows are INCLUDED (same `unload_cost`), and `C_store == C_ful` becomes the site
  dock's sharpest falsifier. `s_recv` stays reported-only and explicitly UNWIRED — a script
  average against a crew constant is `a-right-site-total-hides-two-wrong-shares` rebuilt. The
  pair-scope entry is a sibling `reconcile_pair`, an uncoupled run gets `0 coupled pair(s)`
  rather than a verdict, an absent site DB on a coupled pair is FAIL, the site total is written
  PER BATCH (a run total localises nothing, and the drain is the boundary), and the tool's second
  `SEMANTIC_USES` family is **ordered behind 03's family registration** — `semantics_for` raises
  on an unregistered one. **Two findings:** check 5 carries a dormant false-FAIL on every repack
  row (`role='receive'`, `event_type='repack'` trips `event_type <> role`), silent only because
  `f_repack` is assumed 0.0; and `_charge_repack` early-returns on `self._dock is None`, which
  under coupling is BOTH leaves — the repack counters keep climbing while the seconds vanish
  (recorded against 01's build). **Site crew** added to `CONTEXT.md`; no ADR, the identity rule
  is a consequence of 04's allocation choice.

- [Close the torn-finalize window](issues/16-close-the-torn-finalize-window.md): both precursors
  built (`1b2c572f`, `66515339`). `_finalize_config_run` writes `sim_meta.json` BEFORE it removes
  `resume.pkl`, so the crash window holds a dir that is still resumable instead of one that is
  neither resumable nor complete; and the fresh-run branch of `_plan_strategy_start` now REFUSES
  to `create_run` over a db that already holds a run, the corruption `find_run`'s
  `ORDER BY run_id LIMIT 1` would otherwise make silent. Both mutation-checked. **The gate line
  was wrong and the correction generalises:** both files are in `contract.SHAPE_SOURCES`, so every
  commit this map makes to a simdriver file reddens `preflight --check` and
  `verify_architecture` — the fix is the full preflight (two canaries, ~83 s) plus the arch chain,
  and those canaries ARE the byte-identical acceptance (mixed and store-only, tree shape unchanged
  on `5c9bc35db55b`). `Tests/architecture`'s fifth red is `.claude/worktrees/` only, baselined
  against a `git archive` copy.

- [Exempt repack rows and pin the unload constant](issues/17-exempt-repacks-and-pin-the-unload-constant.md):
  **BUILT and live on `develop`** (`fe80ed92`, `3c54866b`). Check 5's `repack` exemption is by
  (role, event_type) **PAIR** — a PUT row typed `repack` still FAILs and `repack` joins
  put/receive as a word no foreign row may claim, so the exemption widens the vocabulary rather
  than weakening the clause; a **third** spelling of the wrong rule was found in
  `sim_semantics.py`'s `event_type` note and amended (a `Col` note moves no `schema_id`). Check
  6 **holds, and it is measured, not argued**: 505,177-row arms at spread 4.4e-15, and C = 7.6 s
  matches an INDEPENDENT derivation from the run's own pick config — without that second
  derivation the check would pass a dock built entirely from class defaults. **The finding is a
  detector, not a defect**: every run before the per-item charge break (`fc7a46a5`, 2026-09-05)
  fails by ~4,600 s, because that era priced the dock from `UnloadCost`'s CLASS defaults — so
  `per-item-charge-hard-break` is now answerable in one command, and it stays RED because the
  two states separate by eighteen orders of magnitude (the sin the retired contiguity check is a
  monument to is an *inability to tell them apart*, which does not apply). The explanation prints
  ONCE in the summary; sixteen copies read as a broken tool in their own right. **Three more
  findings:** the e2e fixture made check 6 **vacuous** — 7 packs of ONE sku at qty 1, where the
  residual cancels however the price was computed, so zeroing that sku's handle term left the
  check GREEN (the sabotage is what caught it; the fixture is now 25 batches and non-vacuity is
  asserted FIRST); **`C_store != C_ful` today by construction** (store intercept 15, fulfillment
  10 -> 7.6 s and 5.1 s), so 15's cross-leaf clause is a claim about a SITE dock having one
  price list rather than something a coupled run satisfies for free; and check 1's `_TOL` is
  ABSOLUTE, so it fails four archived arms on 1.3e-6 s of float re-association over 4.18M s.

- [Build the coupled work unit and its two-leaf worker](issues/18-build-the-coupled-work-unit.md):
  **BUILT and live on `develop`** (`b8e6c770`, `beda6b77`) — a second leaf now EXISTS. A unit is
  `(label, 'coupled', arm_store, arm_ful)`, two leaves from `_prepare_site_run` (which calls
  `_prepare_channel_run` twice, unforked) driven through ONE batch loop, finalizing two groups
  from the `group_keys` they carry. **The ordering question resolved against the ticket's own
  guess:** section 6 IS separable (moving the site crews to unit scope changes where a value is
  stated, not what is built), but **section 5 is not, and the reason is sharper than the double
  count** — `put_clock` is the absolute carry a BATCH-LOCAL clock list is based from, so one
  site-wide carry over two independent lists starts leaf B's putters where leaf A's finished:
  two full crews serialized as if they were one, a third model and neither on offer. So the two
  site-wide carries move with the RESOURCES (`put_clock` with 04's shared list, `recv_clock`
  with 01's coupled coordinator) and this ticket landed before 04. **That corrects the
  acceptance: absolute numbers do NOT move here**, and what replaces it is stronger — coupled is
  pinned row-for-row identical to the two units it replaces, so 04's break arrives as a failing
  test that names the leaf rather than a measurement after the fact. Coupling is **declared**
  (`--couple-channels`, five seams, `run_layout.json`'s `coupled` v3 — 03's marker, which 14's
  `select()` refusal had no way to observe until now), because 06 couples the inbound-OFF pole
  and so no flag implies it. **Two deviations under force:** `_Leaf` holds CLOSURES, not fields
  — 104 names cross from setup into the loop or tail and 45 are rebound, so a state object meant
  hand-rewriting 775 lines where any miss is silent, while `nonlocal` moves both verbatim; and
  02's "build both partitions first" could not be built and did not need to be, because each
  leaf loads its own inventory, so the assertion it asked for is made on the SUM instead — the
  failure guarded is a silently empty leaf, not an exception. 11's `expected_pick` refusal is
  retired: a unit returns one result per leaf, each stating its own arm. **Five findings:** the
  nine source-inspecting guards would have gone on passing while guarding nothing (two had to
  become structural rather than literal); a backbone edge had to be re-anchored on the NESTED
  closure `_step`, because the extractor attributes a call to the function it is written in; the
  descriptor's field set IS verified against its writer while `context/artifacts.yml`'s is not,
  so the schema alone would have left the catalogue stale and green; `--catalog-merge` seeded
  both new files' `purpose` from a docstring FRAGMENT again, escaping the fill step; and a
  result dict had to start stating its own arm — 11's "state, don't slice" rule, unfixed on the
  result side and invisible while every unit had one arm.

- [Build the site put-away pool and the site put clock](issues/19-build-the-site-putaway-pool.md):
  **BUILT** (`9b0e21e8`, `59a9927b`) — and **this is the map's first real comparability break**,
  measured per leaf rather than argued. `Inbound/putaway_pool.PutawayPool` binds both channels'
  put queues to ONE `list[float]`, so a putter genuinely takes work from either queue; it owns the
  day's division (each leaf at a CUMULATIVE share of `derived.put.expected_utilization`, then
  every leaf again against the whole day), the once-per-site-day reset, and the site `put_clock`
  based at the site day start. The uid block starts at `max(k_pickers)` so a putter is the same
  person in both DBs — and the receiving cursor had to chain off the POOL's block end, or the
  smaller leaf's receivers land inside the putters'. **The structural change: a batch is now TWO
  HALVES.** 04 section 9 assumed a loop order 18 did not build, and under whole-batch-per-leaf the
  residue pass fires after the earlier leaf has already snapshotted its queues and stamped its
  rows — placements real, rows a day late, `put_queue_state` reporting a depth that never stood,
  nothing raising. So `_build_leaf` returns `replenish(i)` beside `step(i)` and a unit runs every
  leaf's replenishment before any leaf's picks; 79 lines moved verbatim, six names crossing.
  **Four refusals, two more than the ticket named:** no derived staffing block (coupling is an era
  feature — the fallback is the double count with a coupled label on it), no whistle, a grid that
  is not one batch per site day, and `put_queue_split`. Measured on a 12-batch pair with a site
  crew of 2 (uncoupled: 2 PER LEAF): store put rows move mean 50.7 s, fulfillment mean 507.7 s and
  its actors move `[20, 21]` -> `[25, 26]`; put seconds move too (+0.7% / -4.6%), because the
  two-pass drain changes which unit reaches which bin. Uncoupled is byte-identical, diffed row for
  row against a `git archive HEAD` copy over 4,917 events, 36 of them puts. **Three findings:** the
  e2e fixture is too thin to measure the break (3 batches produce ONE put row), so the measurement
  lives outside the suite and the test asserts the relationship instead; `--catalog-merge` seeded
  the `purpose` from a docstring fragment for the THIRD time, and a multi-line fill additionally
  breaks `test_merge_is_idempotent`; and `render_html --build` needed a second pass for an
  `OSError`, the "run it twice" shape from a different cause.

- [Build the site space view](issues/20-build-the-site-space-view.md): **BUILT** —
  `Inbound/site_space.compose_site_view`, one pure function over already-frozen views, and
  `SpaceTimeline.freeze` is untouched (single-manager signature, purity pin intact). A leaf's
  `empties` is the WHOLE geometry's free index, so every leaf lists the other channel's bins as
  permanently, falsely free; the composer FILTERS `empties` per contributing leaf and unions
  `predicted` and `emptied_at`, which are already leaf-own — so the composed key set is
  **partitioned by regime** and a mixed trailer's store units rank against store bins. `versions`
  compose element-wise (a sum is lossy in exactly the failing direction), `frozen_at` is one site
  epoch and a disagreement is refused, `released_at` is carried per regime and never averaged, and
  the futuresight window is REFUSED with its zip rule written on the refusal rather than shipped
  unexercised. **Wired live at the coordinator's one leaf**, where a composition of one returns its
  view BY IDENTITY — the seam goes in ahead of what it serves, as 09/11/12/13/16 did, so the guards
  are mutation-checkable now instead of in two tickets' time. **Three findings:** the leaves hold
  TWO warehouses and `emptied_at` is keyed by `id(bin)`, so a union could hand one leaf's lookup
  the other's stamp for a different bin (refused); `tuple(x)` on a tuple returns the same object,
  so the first "the tuples are rebuilt" mutation was a no-op that read as a passing test; and one
  claim — that the drain still routes through the composer — is unobservable at one leaf by
  construction, so it is asserted on the SEAM rather than on the data or the source. Standing-yard
  byte-identity measured at **98,676 rows across 9 tables**, wall clock aside; 20 new tests; 13
  mutations, 13 caught.

- [Build the coupled resume reconciler](issues/22-build-the-coupled-resume-reconciler.md):
  **BUILT** — `_reconcile_coupled_unit` in `workunits.py`, with `_leaf_is_complete`,
  `_arm_db_path`, `_meta_path`, `_site_db_path` and `_forget_arms`; `coupled` threaded down to
  `_plan_strategy_start` so the batch-grain refusal reaches the planner; `_supervise` declares
  `mid_flight` on any retry. **10's repair surface was THREE things and is actually FOUR:**
  `reset_strategy_db` cannot touch `resume.pkl`, and the record is the planner's fallback — so a
  reset arm that keeps its record runs an empty loop over a deleted DB, in silence. **And 10
  missed a torn state:** per-leaf CHECKPOINT SKEW, which wedges a coupled run permanently because
  every later `--resume` reproduces the same refusal. It is a repair rather than a second refusal
  because the two leaves are checkpointed in ONE loop by ONE process microseconds apart, so the
  reachable hazard is the gap, never a divergence of grain. **Two more findings:** the run-tree
  ratchet counts PROSE, so heavy docstrings tripped it with no new hand-joined path — invisible
  in the totals (the gate is red on HEAD) and visible only by diffing the per-file list against a
  `git archive` copy, after which the file sits BELOW its baseline; and `_prepare_channel_run`
  carried two spellings of an arm's db path, where the second would have removed nothing and left
  the rows to be appended to. Resumed-uncoupled byte-identity measured at **410,338 rows across 4
  arm DBs**, against a HEAD copy overlaid with only this ticket's two source files — the working
  tree carried two other sessions' edits, so a plain HEAD-vs-worktree diff would have been
  contaminated. 15 new tests; 9 mutations, 9 caught.

- [Re-shape the funnel spec for arm pairs](issues/23-reshape-the-funnel-spec.md): **BUILT** —
  `PHASE2_ARMS` becomes `PHASE2_PAIRS`, an ordered list of `(store_rule, fulfillment_rule)`;
  `CHANNEL_RESTOCKS` is DERIVED from it by `channel_restocks_for` rather than authored beside it;
  `strategies_for` now preserves the caller's rule order (it re-imposed the grid's, which throws
  away the only thing a diagonal reads) and REFUSES a `set`; and `get_spec` shape-checks every
  spec through `validate_spec`, so a malformed campaign stops before a run directory exists.
  **06 section 0's own derivation was written as `{p[0] for p in pairs}` and a SET destroys the
  rank** the zip in `_prepare_site_run` IS the pairing on — the columns are ordered tuples.
  `whatif_config.py:166-170`'s false cross-phase claim is rewritten. **The driver-side install
  was wired by the parent session** (the path was fenced for parallelism, not by the ticket):
  `_run_whatif_matrix` now installs what the derivation says and never authors an arm set, and
  **two branches came out because no input could reach them** — the cells-based arm-set refusal,
  which `validate_spec` subsumes strictly (a cell's `inbound` is built from the spec's own axis,
  so they cannot disagree, and the cell form additionally let a single-cell inbound spec through),
  and the guard that skipped the flat `fifo` check for pair specs, which a pair spec satisfies by
  construction because the rider is required as a PAIR. Both were found by mutation: disabling
  each changed nothing. **One sub-item is deliberately NOT spec-side** — "more than one config per
  channel" cannot be checked from a spec, because configs come from `sim_config` and the command
  line and no spec can produce the shape `_prepare_site_run` refuses; the earlier gate belongs
  beside `_check_era_flags`, and needs to know whether the catalogue is mixed or it would refuse
  runs that work today. **A finding for every later neutrality check:** figures are NOT
  byte-reproducible run to run — a HEAD-vs-HEAD control produced 51/51 pixel-differing PNGs
  (compared on IDAT, not metadata), so a check that diffs figure bytes reports a break that is not
  there. Neutrality measured at **762,769 DB rows across 28 tables, zero differences**; 68 tests;
  21 mutations, 21 caught.

- [Build the coupled receiving coordinator and the site recv clock](issues/21-build-the-coupled-receiving-coordinator.md):
  **BUILT** — `SiteReceiving` now serves N leaves over one dock and one yard: a `{sku: leaf}`
  owner dict routes a bare lot at step 1, `regime_of(item.unit)` routes the unit at step 4,
  and the two are CROSS-CHECKED (the only shape in which the catalogue partition and the
  regime tagging can disagree is a ledger balancing in the wrong warehouse). The site
  `recv_clock` is based the way 19 based `put_clock`, `open_batch` is idempotent per day and
  the carry commits on the LAST leaf's report; six leaf accessors REFUSE once the scope is
  the site's — 01 named four, and the two added are worse than a wrong level (the first leaf
  to drain the shared dock takes the other channel's rows and restarts the crew's clocks
  mid-batch). **01's interleave was wrong for phase 1:** `_advance_lead_queue` delegates to
  the ONE transit, so two leaves ticking it land every supplier lead a batch early, silently
  — `SITE_PHASES` makes it site-scoped, and `bind` asserts every leaf holds the coordinator's
  transit so "driven on the first leaf" is the same call either way. **And the interleave is
  PHASE-MAJOR because that is what makes a trailer MIXED:** leaf-major loading departs the
  open trailer between the two channels' loads, so every trailer would carry one channel's
  merchandise and the site dock would be two docks wearing one name. **The acceptance is
  corrected the way 18's was: NO comparability break lands here** — the break is a coupled
  run FIELDING one dock, and that needs the `_site/` artifact, `SiteGainBundle` and the
  unload price, all three on this ticket's own exclusion list; the coordinator is complete
  and 24 wires it. **The unload price is GRADUATED, not taken**
  ([27](issues/27-decide-the-site-docks-unload-price.md)): it got sharper rather than
  obvious, and acquired a third candidate — a price LIST keyed by the unloaded unit's regime,
  which this build makes natural and which would retire 15's `C_store == C_ful` by
  construction. The coordinator is HANDED a priced dock and never builds one, so all three
  answers stay reachable. Uncoupled byte-identity measured at **149,277 rows across 19
  tables**, receiving evidence counted first (76 receive rows, 20 yard drains, 22 trailers)
  and measured twice, before and after a review round; 36 new tests; 29 mutations, 29
  caught. **The review round found the defect the build made and the docstring denied:** the
  six refusals took the dock's clock RESET away from `Dock.drain_records` and appointed no
  successor, while `note_records` claimed in writing that the reset had one owner — silent,
  cumulative, and exactly the failure `drain_records`' own docstring records. A refusal that
  takes a capability away owes the same commit a replacement. **And a finding for every
  helper-copying session:** four test helpers each carried a copied comment saying a
  coordinator over a v1 transit was "harmless", and none of the four was true once the
  refusal existed.

## Not yet specified

- **Nothing here needs a DECISION any more.** Every design ticket on this map is resolved, and
  the "remaining builds" lump that used to sit in this section has been charted in full: tickets
  20-26 hold it, item for item. The route to the destination is therefore visible end to end, and
  what is left in this section is only what is genuinely still dim (below) plus the two decisions
  that cannot be taken until the code they are about exists — both of which are named inside the
  tickets that will surface them ([Build the coupled receiving coordinator and the site recv
  clock](issues/21-build-the-coupled-receiving-coordinator.md) for the site dock's price list,
  [Build the site crews' cross-leaf checks](issues/25-build-the-site-crew-checks.md) for
  `receiving_report`'s tolerance). See **The route** under Notes for the ordering and what runs in
  parallel.

- **Within-day put interleaving.** The charter shares a DAY budget, so a putter cannot take the
  earliest-ready cart across channels mid-day. Whether that changes the answer is dim until a
  coupled run shows a day where one channel's put queue actually starves while the other's crew
  sits. The faithful version — both leaves on one time-ordered loop — is a cadence change of the
  same family the inbound map ruled out, so it graduates only with evidence. **19 moved the price
  of it**: the batch is now two halves (`replenish` then `step`) across every leaf, so a third
  interleave point exists that did not before. The residue pass is the cheap approximation and it
  is now built; what is still dim is whether the expensive one buys anything.
- ~~**One unload price for the site dock.**~~ **TICKETED — no longer fog.** The coupled
  coordinator now exists, and 21 found the question is not merely well-posed but has a THIRD
  answer nobody had named: a price LIST keyed by the unloaded unit's own regime, which the
  `regime_of` route at the handoff makes natural and which would retire 15's `C_store == C_ful`
  by construction rather than satisfy or falsify it. That is a decision, so it graduated as
  [Decide the site dock's unload price](issues/27-decide-the-site-docks-unload-price.md)
  (`grilling`, open). 21 left the coordinator HANDED a priced dock and building none, so all
  three answers stay reachable; **24 cannot build the site dock until this closes.**
- **`receiving_report`'s absolute tolerance.** `_TOL` is 1e-6 SECONDS, compared against sums
  that grow with the row count: on a 505,177-row arm checks 1's two surfaces accumulate 1.3e-6 s
  apart over 4,177,040.9 s — a relative error of 3e-13 reported as a FAIL, on four archived arms
  today. Absolute-versus-relative is a decision (a relative tolerance hides a small real
  discrepancy on a large arm, which is the failure check 1 exists for), and it grows sharper
  under coupling, where a site total is the sum of two leaves'. Not ticketed because the right
  form is not yet clear.

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
