# Design the site scope in the run tree

Type: grilling
Status: open

HITL. Skills: `grilling` + `domain-modeling`.

## Question

The charter keeps `<channel>` as a run-tree directory level (`runschema/schema.py:63-74`,
`AXES` `:78`) — what changes is the UNIT, not the TREE. That leaves one thing genuinely homeless:
**what is site-scoped has no place to live.** Under one yard, the trailers, drains, door spans and
fees belong to the site, and writing them into each arm's channel DB writes them twice.

1. **Where site-scoped artifacts go.** `strategy_runner.py:1423-1424`, `:1947`
   (`mgr.drain_yard_trailers/_drains`, `save_yard_trailers`) write yard rows into this arm's sim DB.
   A site scope beside the channel leaves (the way `<run_root>/_dossier/` is a fourth scope — memory
   `run-scope-dossier`) is the obvious candidate, but so is "one elected leaf writes, the other
   doesn't". Decide, and decide what a channel leaf still reports about inbound (`transit_snapshot`
   / `in_transit_qty` / `_entries` would otherwise report the **site** level to a leaf that thinks
   it reports its own — `Inventory_Management.py:254`, `:620-648`).

2. **The rows that stamp a channel and shouldn't.** `Picking_Data.py:214`, `:239`
   (`_IDENTITY_COLS` includes `channel`), `:449-452`, `:1372-1376` stamp one channel on every sim
   DB, and the per-arm timeline caveat says a cross-channel Gantt is fiction. Under one site dock
   the receiving rows **are** cross-channel and legitimately comparable: the caveat and the stamp
   both become wrong. Decide the new stamp (a `site` value? a nullable column? a separate family?)
   — and route it through the schema pipeline, never a consumer edit (CLAUDE.md §2: `--sync` before
   the DDL edit, `--accept` after; `schema-maintainer` owns it).

3. **`runtime_metrics`.** `UNIQUE(cell,pair,config,channel,arm)` (`runtime_metrics.py:31`, `:63`,
   `:151-172`, `:241-248`) — one coupled process produces one set of metrics for two channels.
   A `channel='site'` row, or a schema change? Remember `runtime-metrics-is-the-deep-instrument`:
   the deep tier's `t_*` are MEAN seconds per batch per arm, so whatever is chosen must still
   divide by something meaningful.

4. **The completeness marker.** `runlayout.py:121-166` (`ChannelRun`, `group_key`,
   `iter_channel_runs`) makes `sim_meta.json` the per-channel completeness marker, and
   `_finalize_config_run` (`supervisor.py:24-54`) writes one per `run_dir`. One coupled unit
   finalizes two leaves at once — decide whether both get their own marker and what makes the PAIR
   complete.

5. **The coupled marker itself.** `sim_manifest.py:124-197` carries a `channels` list plus a
   per-config `'channel': channel` stamp. Something must say "this run was coupled" — every
   downstream refusal in ticket 07 reads it, and `resolver.py:128`, `:157-172`, `:304-320` (the
   positional relpath splitting and `{channel?}` expansion) must keep working either way. Note the
   standing trap: the store *config* and the store *channel* share a name (`:313-320`), so consume
   levels positionally, never by directory name (CLAUDE.md §3).

6. **The vocabulary.** `sim_semantics.py:112`, `:367` declares that a channel leaf counts the other
   channel's bins as foreign (memory `free-bins-counts-the-whole-geometry`). A coupled inbound makes
   some quantities genuinely site-scoped — decide the scope vocabulary before the first quantity
   needs it.

7. **The two leaves share no ancestor below `<pair>/`** — found by
   [Design the coupled work unit](02-design-the-coupled-work-unit.md) and harder than this
   ticket's framing assumed. `config` sits **above** `channel` in `LEVELS`
   (`runschema/schema.py:63-74`), and the two channels draw from different config sets
   (`CONFIG['channels'][name]['configs']` — `store` and `ful_calibrated` today), so a coupled
   unit's leaves live at `<pair>/store/store/` and `<pair>/ful_calibrated/fulfillment/`. A
   site-scoped artifact therefore cannot live under `<config>/` at all: the only directory that
   dominates both leaves is the pair. Whatever scope this ticket creates has to sit there or
   above, or the tree needs a level it does not have.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§2.
