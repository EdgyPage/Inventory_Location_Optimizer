# 05 - a run that starts empty and receives its declaration through the yard

Type: task
Status: open
Blocked by: 04

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
