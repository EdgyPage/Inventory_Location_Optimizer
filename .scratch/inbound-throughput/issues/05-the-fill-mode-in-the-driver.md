# 05 - a run that starts empty and receives its declaration through the yard

Type: task
Status: wontfix (closed 2026-09-23; was: claimed)
Blocked by: 04 (for PRICED fill cells at scale only; the mode itself is built)

No such path exists. Every run places its stock declaration at the freeze (cause `initial`)
by the placement rule, and the yard carries reorders only. A **fill trial** (CONTEXT.md) needs
the driver to do the opposite: freeze the declaration but place none of it, dispatch it as
trailers, and let the unloading policy under test decide the order it lands in. Blocked by 04
because a fill's yard is deep (map Notes: ~2,500 trailers over the fill) and the exact plan at
depth 200 is 40,000 placements per drain.

## The decisions this implements

- **Fill, then pick (Q16).** Two stages in one unit: a fill stage with NO pick demand, then a
  pick stage that is an ordinary 40-batch era run (base stock, reorders, the derived crews)
  whose only difference is where it starts (Q22). The unit's batch counter runs across both;
  the pick stage's batches are the ones every 40-batch instrument reads.
- **Staffing is derived, not understaffed (Q15/Q20).** The fill declares a span of 40 site
  days; the receiving crew is derived from the declaration over that span (the ADR-0004
  shape: demand declared, crew derived) -- ~4x the campaign's crew on the 400k catalogue. Depth
  comes from a declared arrival-to-drain ratio, 0.95: the dispatch rate is that ratio times
  the derived drain rate, and the yard is a queue near saturation, which is where the
  campaign's own regime sits (0.82, depth 17-24). The site dock stays shared across channels
  (`site-dock-is-shared-across-channels`).
- **Dispatch order is a seeded random world order (Q21)**, one draw every arm shares, keyed
  like the leads (`SEED_WORLD`, tag, seq), so trailer #N carries the same packs in every arm.
  The packing is the declaration's own (CONTEXT.md: Stock declaration -- the packing the
  warehouse was sized from), so the fill rebuilds the same tier mix a reorder would.
- **Refusals.** A spec naming `inb_off` (places nothing in a fill) or a trailer bound
  (refuted) is refused at spec build, the way `_check_campaign_pin` refuses an unnamed pair.
  A fill on a catalogue whose declaration the warehouse cannot hold is already refused by the
  declaration itself.

## Where it touches

`sim_assets.build_shared_assets` (freeze without placing: the declaration is written, the
`initial` placements are not), `strategy_runner._build_leaf` / `_build_arm` (the stage
boundary and the fill stage's empty demand), `Inbound/transit.py` (dispatching a declaration
rather than a reorder), `simconfig/staffing.py` (the fill crew), `whatif_config` (the run
defaults: span, ratio, mode). The batch precompute serves the pick stage only. Resume: the
fill stage checkpoints like any arm; a torn coupled pair is refused as today.

## Byte-identity

The fill mode must be a strict no-op when off: the toy digest IDENTICAL, and the flat pool's
`test_work_pool.py` barrier unchanged. The fill itself has no reference to digest against;
its pick stage is byte-comparable across CELLS of one run (same script, same seed, only the
placement differs), which is what Q16 bought.

## Bar

The tiny smoketest runs a fill trial end to end on the tiny catalogue (fill stage, pick
stage, both leaves, the site DB with its yard tables) and `run_digest.py` reads the pick
stage; the schema pipeline is synced for whatever the fill stage adds (the future-work
column of ticket 06 rides there, not here).

## Built 2026-09-22 -- the mode, verified on the toy; one decision is the user's

Built ahead of 04 on the reading that 04 blocks the COST of a priced fill, not its code: the
mode runs fifo cells at any scale today, and a gain cell's fill is exactly as expensive as the
exact evaluator makes it (the toy's gmyopic fill: ~40 s against fifo's ~6 s).

**How it runs.** `INBOUND_FILL_SPAN_DAYS` (None = off) and `INBOUND_FILL_RATIO` (0.95), through
all five seams (settings, CONFIG, knob list, `inbound_spec()['fill']`, CLI
`--inbound-fill-span-days` / `--inbound-fill-ratio`).  `sim_config.fill_spec()` refuses a fill
with no trailers (so `inb_off` cannot run), without the standing yard, under a trailer bound, or
at a ratio outside (0, 1); `run_simulation._check_era_flags` refuses one outside the coupled
era.  The parent derives the fill crews beside the steady state's
(`staffing.declared_work` / `derive_fill`, recorded as `derived.fill`); `workunits._fill_payload`
prices the dispatch rate PER CELL off the SEATED crew (`staffing.fill_dispatch_rate`: doors x
door team caps it), because the door-scarcity axis varies doors across one pair's cells.  In the
worker each leaf DECLARES instead of stocking (`Inventory_Manager.declare_all`), the unit's
`_FillDispatch` sends both channels' lots through the one yard in one seeded world order
(`SeedSequence([seed_world, 0xF111])`) against a cumulative quota, crediting `_deferred_qty` at
dispatch exactly where a reorder is booked; fill days are empty-demand batches through the
existing skipped-batch path (which now flushes its checkpoint window on a fill day).  When
nothing is in transit, at a door, on the dock floor or unbinned, the pool's and the dock's
clock lists shrink IN PLACE to the pick-stage crews and each leaf's script index becomes
`i - fill_batches`.  `fill_batches` reaches `sim_meta.json` beside `expected_pick`.  A fill that
has not settled by twice its dispatch span plus ten days raises with the census.

**Verified.**
- Off: `_toy_priced` on the working tree digests IDENTICAL to the pre-change baseline on all 40
  arms (`run_digest.py`).
- On: `_toy_fill` (fifo + gmyopic, fifo + rank_cartlabor rules, 8k SKUs): 113,393 declared
  units in 8,000 lots dispatched over 6 days, settled after 11 site days (the tail is the
  lognormal lead spread), crews put 158 -> 2 and receiving 115 -> 1 (40 seated at 4 doors x
  10: DOOR-BOUND, and the rate is priced off the 40), 6 pick batches, conservation OK on every
  arm.  Two runs digest IDENTICAL on all 8 arms; every arm's sim_meta carries
  `fill_batches: 11`.  `analyze_run` completes on the fill root.
- `Tests/unit/test_fill_trial.py` (26 tests); the unit tier (3,261) and the CLAUDE.md
  instrument selection pass; the schema gates are current (the fill adds no table or column).
- Smoketest: a new `fill` profile runs preconditions / simulate / analyze / verify_tree.
  simulate and analyze pass; verify_tree fails on two gaps that fail EVERY coupled run
  (shown on the ordinary `_toy_priced` root too): the preflight observer does not resolve an
  arm-PAIR site DB stem to `{strategy}`, and the leaf check expects the full 34-arm grid.
  Filed as a separate task.  The two verify_tree misclassifications that WERE fill-relevant
  are fixed: `site_plan_trace` (ticket 03's artifact) is classified coupled-only, and the
  channel rollup is must-absent on a coupled run (the writer declines it by design).

**What a reader of a fill run must know (for 06).**  The arm's tables hold the fill days as
zero-duration skipped batches `0 .. fill_batches - 1`; the pick stage is `fill_batches ..`.
Every 40-batch instrument must select it -- the analysis suite does not yet (it reads all
batches), which is 06's to wire with the score.  The fill days write no `shift_days` rows
(the skipped path never did).

**THE OPEN DECISION: what the uniform stock mode means in a fill.**  In the campaign `uni_*`
means "the initial stock is placed by the manager's default placement, reorders by the arm".
A fill has no initial stock -- everything is a receipt -- so the mode governs nothing, and each
way of giving it a meaning changes the experiment:
  (a) place the fill by the default (uniform-random) placement, the arm's rule from the pick
      stage on -- but the gain evaluator prices every trailer by the ARM's rule (it reads the
      policy record, never `mgr.placement`), so the unloading policy would optimise an
      objective nothing enacts; uni becomes a control where order cannot matter, and Q26's
      "both stock modes agree" would fail by construction;
  (b) place it by the arm's rule -- then `uni_*` is a byte-for-byte copy of `opt_*`, and Q26's
      agreement is vacuous;
  (c) drop the stock-mode replication from the fill trial and replicate some other way (a
      second world-order seed is the natural candidate: same declaration, different trailer
      mix).
Until decided the worker REFUSES a uniform fill arm and `workunits._channel_strategies` drops
those arms from a fill's plan with a log line, so a fill runs opt arms only.

**Code review (2026-09-22), all findings addressed before commit.**
- CRITICAL, fixed: checkpoint markers counted fill days while every planner compares them
  against the pick stage's `n_batches`, so a finished pair re-planned before its cell
  finalized would have run a second fill into its own tables.  Markers are now in PICK-STAGE
  batches (`i + 1 - script_off`; the final pin is `n_script`), a fill day writes none, and the
  driver runs no batch for a finished fill unit and refuses one resumed from the middle
  (pinned by stand-in-leaf tests in `test_fill_trial.py`).
- The fill-day flush now rolls the section and checkpoint windows.  `runtime_metrics` still
  divides whole-arm section totals by pick-stage batches, so a fill arm's `t_*` means include
  the fill's seconds: read them as whole-arm costs.
- `inb_off` / trailer-bound cells and a non-era run default are refused at SPEC build
  (`whatif_config._refuse_unfillable_cells`), not when the cell is prepared.
- The unload-cost overlays are refused under a fill: the rate is priced at the put-away-derived
  unload law, and a dock charging another price would press the yard off the declared ratio.
- `expected_pick` is taken at `begin_pick`, over the placement the fill produced.
- The declaration is released once dispatched; the per-unit price is over DECLARED units.

**For 06: the fill's length can differ between CELLS.**  `fill_batches` is where each cell's
site settled, which the unloading policy can move by a day.  So a pick-stage batch is
`batch_id - fill_batches` per arm, and every reader that pairs arms by raw `batch_id`
(`frames._aligned`, the significance and vs-baseline tables, `measured_floor`, the ranking's
`by_batch`) must re-base first, as must keyframe alignment (`i % keyframe_interval` is on the
unit counter).  The single loading chokepoint is `Performance_Evaluations/core/requests.frame`
(the strategy dict already carries `fill_batches`); four readers go around it
(`site_batch_frame`, `SiteContext.leaf_batch_df`, `_strategy_travel_handling`,
`run_unload_ranking._leaf_batch_rows`).  The alternative worth weighing before building the
re-base: start every cell's pick stage at one DECLARED batch (idle days after settling), which
makes raw batch ids align across cells and reduces the analysis change to a filter.

## Closed 2026-09-23 by the user

The whole inbound-throughput map was closed: the evaluator question is being re-thought from the problem statement, and these tickets will not be relevant by the time it is picked up again.  What was built stays on develop; see the map's closing note.
