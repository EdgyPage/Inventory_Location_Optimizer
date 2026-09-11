# Design the site scope in the run tree

Type: grilling
Status: resolved

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

## Answer

**The scope splits at the PACK, not at the dock — so most of this ticket's premise is false.**
Packing partitions by channel before it packs (charter), so every pack-denominated receiving
quantity has an owning channel and stays exactly where it is today. Only the trailer-denominated
and door-denominated rows are homeless, and they go to one site DB under the pair. Recorded as
[ADR-0005](../../../docs/adr/0005-inbound-scope-splits-at-the-pack.md).

### 1. Where the site artifacts live

**`<cell>/<pair>/_site/inbound_<arm_store>__<arm_ful>.db`, declared `scope: 'pair'`.**

The pair is the only directory dominating both leaves (finding of
[Design the coupled work unit](02-design-the-coupled-work-unit.md)), and the arm pair rides in the
**filename stem**, not a directory — the precedent is `strategy`, which `AXES` (`schema.py:76-78`)
calls a navigation axis that is deliberately not a level.

This is the `_dossier` pattern, not a new level. `_dossier` (`schema.py:209-272`) is eight ordinary
ARTIFACTS whose paths begin with a literal `_`-prefixed segment, `scope: 'run'`, `optional: True`
with a `condition`. A new sibling costs a contract bump the adopt stage performs. **A new LEVEL
costs hand edits in at least six modules** — `AXES`, `resolver.axes()`'s hardcoded population,
`runlayout`'s fixed nesting depth, `preflight._axis_values` + `_generalize_file`'s
`order = ('cell','pair','config','channel')` + `observe`, `_scope_of`, `write_run_layout` — plus
`Tests/integration/test_runschema_contract.py:90-97`, which asserts the level list verbatim.

Declaring it early is free: the canaries never produce it, so it lands in `never_produced`, which
preflight prints and does not fail on (`preflight.py:409`, `:603`).

**Rejected: a run-root DB on the `runtime_metrics.db` pattern.** It has no concurrent-writer
problem only because the PARENT writes it (`supervisor.py:112-116`). Yard rows are per-trailer and
per-batch volume; shipping them home through the result dict re-creates the pickle cost
[02](02-design-the-coupled-work-unit.md) section 2 just finished keeping cheap.

**Rejected: an elected leaf.** It reproduces the per-leaf artefact this map exists to kill, and
renders a yard scorecard on one channel and an empty one on the other — which reads as a finding.

**Found while deciding — the reserved-prefix guard is only at ONE depth.**
`runlayout.iter_channel_runs:144` and `iter_sim_dbs:176` skip `_`-prefixed dirs at the **pair**
level only; the config loop (`:146-149`) and the channel loop (`:153-156`) have no such guard. So
`<pair>/_site/` is walked as a phantom **config** today, and any `sim_*.db` beneath it surfaces as
an extra arm. `RESERVED_PREFIX` (`schema.py:59`) is declared and hashed but **read by no walker** —
all three skips are hardcoded `_` literals. Graduated as
[Harden the three positional seams](11-harden-the-positional-seams.md).

### 2. The scope split — the table

| Stays in the channel's own sim DB | Moves to the site DB |
|---|---|
| `recv_depth`, `recv_unloaded`, `recv_cut`, `recv_seconds` | `yard_trailers` (arrival/staged/emptied/status) |
| `recv_repacks`, `recv_repacked_packs` | `yard_drains` (yard depth, free doors, staged remainder) |
| `work_events` with `role='receive'` | door span, door utilization, `dock_ceiling` |
| `carryover` `reason='dock'`, `reorder_queue` `kind='dock'` | detention, overage, the fee threshold |
| `shift_days.standing_dock` | the receiving crew's denominator |

**So `simulation_runs.channel` needs no new value and no DDL change at all** — the rows that stay
are genuinely that channel's, and the stamp stays honest. The ticket's premise that "the stamp
becomes wrong" holds only for the right-hand column, and those rows leave the file rather than
acquiring a sentinel.

**The one read-out that moves despite a decomposable numerator:** a leaf's `recv_seconds` over the
**site** receiving crew. `yard/scorecard.py:55-92` `_receiver_busy` and
`equilibrium._utilization_clause` (`:860-897`) both do exactly this, dividing one leaf's seconds by
`exp['departments']['recv']['crew']`, which `staffing.py:710-712` states outright is a site total.
That is a share of a whole the channel does not own — memory
`a-right-site-total-hides-two-wrong-shares`. It is a site quantity; it moves.

### 3. Two clocks in one column

[02](02-design-the-coupled-work-unit.md) section 5 made `recv_clock` site-wide and left `arm_clock`
per leaf. Both stamp `work_events.t_abs`, in the same per-leaf table, with nothing to tell them
apart.

**Discriminate the clock tag by `role`.** `Schema/semantics.py` already supports it —
`resolve(family, table, column, row=...)` takes a `ByDiscriminator` that picks its case from the
row, and raises without one.

**Amend the caveat, do not delete it.** `Picking_Data.py:449-452` and the machine-readable twin
`SIM_CAPABILITIES[CAP_WORK_EVENTS].caveat` (`:1369-1384`) both say a cross-channel Gantt is
fiction. Under coupling that stays true of pick and put and becomes **false of receive** — the one
genuinely new thing a coupled dock produces. The caveat narrows; it does not go away. Note the
caveat is payload, not prose: consumers carry `provenance(cap)` into what they emit
(`SCHEMA_COMPATIBILITY.md:180-185`).

**Rejected: re-basing receive stamps onto the leaf clock.** The cheap option, and it would destroy
the only cross-channel timeline the coupling creates.

### 4. `runtime_metrics` — and a silent corruption waiting for the coupled uid

`supervisor.py:114` calls `record_arm(run_root, cell, uid, res)`, and `record_arm:156` unpacks
`pair, config, channel, arm = uid` **positionally**. [02](02-design-the-coupled-work-unit.md)'s
coupled uid is `(label, 'coupled', arm_store, arm_ful)` — same arity, different meanings. So the
first coupled run writes `channel = <arm_store>` and `arm = <arm_ful>`, and `channel` is
`TEXT NOT NULL` whose IntegrityError the supervisor **swallows with a warning**
(`supervisor.py:115-116`). A wrong value would not announce itself.

**One row per coupled unit: `config='coupled'`, `channel='site'`, `arm='<arm_store>__<arm_ful>'`.
And `record_arm` takes its four fields by keyword, never by unpacking the uid.** No UNIQUE change —
SQLite cannot ALTER one and a rebuild is the expensive path; `INSERT OR REPLACE` on
`(cell,pair,config,channel,arm)` still keys one row per coupled unit.

The timings stay meaningful under `runtime-metrics-is-the-deep-instrument`: the deep tier's `t_*`
are mean seconds per batch per arm, and one batch is one site day for **both** leaves
(`staffing.py:723-727` refuses channels with different batch counts), so the denominator is real.

Note the existing fourth spelling this sits beside: `run_map_precompute.py:202` keys
`WHERE channel=?` with `run.channel or ''` while `record_arm` wrote `store`, so that UPDATE
matches nothing today and logs `(no row to update)`. Folded into
[Harden the three positional seams](11-harden-the-positional-seams.md).

### 5. The coupled marker, and what makes the pair complete

**One marker: `coupled: true` plus the arm pairs, in `run_layout.json` at the run root.** Adding a
key there changes **no contract id** — `contract._shape_only` (`:139-163`) hashes the DECLARATION
(features, prefixes, axes, levels, artifacts), never a descriptor instance.

The reason a per-leaf copy looked necessary is the spawned-analysis-worker seam (memory
`config-is-not-a-channel-to-an-evaluation`), but it does not apply: `EvalContext` **already**
anchors itself on the directory holding `run_layout.json` (`core/context.py:28-30`), precisely
because a fixed dirname-hop count cannot be right for both tree shapes. So the leaf can read the
run-root marker directly, and a second copy in `sim_meta.json` would be a second thing to rot
(CLAUDE.md section 6).

**Pair completeness needs nothing new.** Both leaves keep their own `sim_meta.json`, written by two
`_finalize_config_run` calls (`supervisor.py:24-54`, keyed on `run_dir`). A coupled unit finalizes
both or neither, because [02](02-design-the-coupled-work-unit.md) section 1 carries `group_keys`
and a group finalizes only when every member succeeded. `iter_channel_runs` is unchanged.

### 6. The scope vocabulary

**The FILE carries the scope. Nothing is added to `Col`.** A table in the site DB is site-scoped by
construction, the same way a leaf's directory carries its cell, pair, config and channel.

This is deliberate, because "site" is **not a grain**: a `yard_drains` row's grain is `batch` and
its scope is the site. The two are orthogonal, and `grain` is a free-form string validated by
nothing (`Schema/semantics.py:74-101` — `__post_init__` checks `kind` and `clock` only). Adding a
`site` grain would conflate them *and* be unenforced. The existing precedent agrees: today's only
in-record scope marker anywhere is `workunits.py:245`'s `catalogue_scope: all_regimes`, and the
only cross-channel semantics in the whole file is prose inside `free_bins`' `note=`
(`sim_semantics.py:108-116`, memory `free-bins-counts-the-whole-geometry`).

**`site` is minted here as a fourth evaluation scope** — `per_strategy | config | aggregate | run |
site` (`core/registry.py:22-31`) — so ticket 07 inherits the name rather than inventing it. A site
context sees both channels of one pair under one arm pair; no context class sees that today
(`core/context.py` has `EvalContext` = one leaf, `AggregateContext` = cross-profile within one
cell x config x channel, `RunContext` = the run root; there is no cell or site context). The
`yard` chart family (`core/families.py`, `scope='leaf'`) moves into it.

**And the scope string gets validated against a tuple.** `registry.evaluation`'s `scope=` is
checked by nothing; the only enumeration is a comment. An unvalidated scope on a family that
renders into the wrong tree is silent, and there are about to be five of them. Folded into
[Harden the three positional seams](11-harden-the-positional-seams.md).

### 7. The config-above-channel finding

Absorbed: it is why section 1 lands at `<pair>/_site/` rather than under any config, and why the
arm pair is a filename stem rather than a directory.

### Written in this session

- [`docs/adr/0005-inbound-scope-splits-at-the-pack.md`](../../../docs/adr/0005-inbound-scope-splits-at-the-pack.md)
  — the pack split, its three rejected alternatives, and the storage home as a consequence.
- `CONTEXT.md` — added **Channel** (absent, though five entries leaned on it, including Site dock)
  and **Scope** (beside Grain, Clock, Unit of account); amended **Clock** for the origin
  distinction section 3 creates. Not added: leaf, work unit, channel run, coupled run — harness,
  not domain.

### What this hands onward

- **[Re-scope the analysis surfaces to the site](07-rescope-the-analysis-surfaces.md)** is
  unblocked, and inherits the section 2 table as its work list, the `site` scope name, and the
  marker every refusal reads.
- **[Harden the three positional seams](11-harden-the-positional-seams.md)** — new, takeable now,
  byte-identical: the reserved-prefix guard at every depth, `record_arm`'s positional unpack, and
  the unvalidated evaluation scope.
