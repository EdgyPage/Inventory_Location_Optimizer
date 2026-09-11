# Design the coupled work unit

Type: grilling
Status: resolved

HITL. Skills: `grilling` + `domain-modeling` + `codebase-design`.

## Question

Today a work unit **is** one arm of one channel:
`uid = (label, cfg_name, channel_key, strategy)` (`Optimization/simdriver/workunits.py:902`), one
`args` dict with one `channel_regime`, one `db_path`, one `run_id`, one `mgr`
(`strategy_runner.py:519`, `:584`). The charter makes a coupled unit **(pair, config, arm-pair)** —
two channel leaves in one process. Decide the shape.

1. **The uid and the supervisor keys.** The channel axis leaves the uid and an arm-pair axis
   enters. `supervisor.py:86` (`gk = uid[:3]`) and `:222` key on the 4-tuple. What is the new
   tuple, and does the inbound-off unit keep the old one (the charter says flag-off is
   byte-identical, so it must)?

2. **The worker entry.** Does `_run_strategy_worker` grow a coupled sibling, or does `impl` split
   into a per-channel **leaf builder** called twice under one coordinator? Remember CLAUDE.md §2:
   spawn, not fork — the entry point and its arguments stay module-level and picklable, and a
   payload carrying two channels' streams is a bigger pickle.

3. **The prepare.** `_prepare_channel_run` (`workunits.py:133-466`) prepares exactly one channel:
   one run dir, one batch stream (`_channel_batch_plan`, `:117-131`), one `ch_pick_cfg` / `ch_wp`,
   one set of yardsticks (`:341-348`), one `_identity` with `channel=ch.name` (`:351-357`), one
   `sim_skeleton` (`:447-465`). Design the site prepare that produces one payload with both
   channels' streams, pick configs, wp, yardsticks and DB paths — and decide how much of the
   per-channel function survives as a callee.

4. **The regime filter.** `strategy_runner.py:740`, `:769-771` filter `inventory.orders` in place
   by `channel_regime` before anything else. A coupled worker keeps the **whole** catalogue and
   partitions it into two managers. Decide where that partition happens and what each manager's
   `regime_bins` / `denom` / fill-rate carry (`:901-908`, `:1840`, `:1960` — two denominators, one
   per leaf).

5. **The clocks.** `recv_clock`, `put_clock`, `arm_clock` (`strategy_runner.py:1207-1218`) are three
   absolute carries per arm. The charter settles the day boundary (shared by construction —
   `staffing.py:723-727`) and put-away's day budget (ticket 04). What remains here: which of the
   three become site-wide carries on the coupled unit, and what a leaf reports as *its* span when
   the memory `calendar-span-is-not-work-days` already warns that spans over-read ~3× on the wrong
   denominator.

6. **The crew payload.** `workunits.py:366-369` hands each leaf the whole derived site crew for put
   and receiving — the double count the map names. Decide what the coupled payload carries instead,
   and how `_check_declared_crew` (`strategy_runner.py:541-581`) verifies it.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§1–§2.

## Answer

**The coupled unit is `(label, 'coupled', arm_store, arm_ful)`, its two group keys are carried
rather than sliced, and `_run_strategy_worker_impl` splits at the line it already splits on: a
per-channel leaf builder called twice, then one batch loop over both leaves.**

### The charter's shape cannot be built as written

**`config` sits ABOVE `channel` in the tree** (`runschema/schema.py:63-74`) and the two channels
draw from **different config sets** — `CONFIG['channels'][name]['configs']`, one each today, named
`store` and `ful_calibrated`. So a coupled unit's two leaves live in two *different* config
subtrees:

```
<cell>/<pair>/store/store/
<cell>/<pair>/ful_calibrated/fulfillment/
```

There is no common ancestor below `<pair>/`. The charter's "(pair, config, arm-pair)" assumed one
config shared by both channels; no such thing exists. This also sharpens ticket 03 — the site
scope is not merely homeless, it has no *ancestor* directory short of the pair.

Two further facts that shaped the rest:

- **The uid is in-memory only.** `resume.pkl` stores `run_ids` keyed by *strategy*; `done_uids` and
  `members` never reach disk. So the uid's shape is free to change, and "byte-identical" constrains
  run **output**, not this tuple.
- **`_run_strategy_worker_impl` already splits where coupling needs it:** `strategy_runner.py:584-1291`
  is setup (inventory, warehouse, manager, dock, transit, crews, DBs, resume planning); `:1292`
  onward is the batch loop.

### 1. The uid and the group keys

**Stop deriving the group key from the uid; carry it.** The payload gains `group_keys` — `[uid[:3]]`
on a flag-off unit (identical behaviour, nothing else changes), two entries on a coupled one.
`_run_pool` (`supervisor.py:86`) iterates them instead of slicing. `members` stays what it is, a set
of uids per group, so a group still finalizes only when every member succeeded — and a coupled
unit's failure correctly blocks **both** leaves from finalizing.

The uid: **`(label, 'coupled', arm_store, arm_ful)`**, arity 4 so `_tag_of` and every existing key
path read unchanged. Flag-off keeps `(label, cfg_name, channel_key, strategy)` verbatim.

The literal `'coupled'` in the config slot is deliberate and honest: the unit is a member of
**neither** config subtree, and a synthetic config name that looked real would invite a walker to go
looking for its directory.

**Rejected: a real `site` config level.** It would put a third name in a tree whose consumers split
relpaths positionally (`resolver.py:157-172`), and a level that exists for one run shape and not
another is exactly what the `{channel?}` conditional already costs us.

### 2. The worker entry

**Split `impl` at 1292: `_build_leaf(args, channel) -> Leaf` for the setup half, then one batch loop
over both leaves.** Everything the setup half touches is already per-channel; the batch loop is the
only genuinely coupled part, and it is where ticket 01's coordinator sits between the two leaves'
phases.

**Rejected: a coupled sibling entry point.** It would duplicate ~700 lines of setup that differs in
nothing, and that half is also where resume planning lives — a forked copy would rot.

On the pickle (CLAUDE.md §2, spawn not fork): the coupled payload is roughly 2x today's, and the
heavy items are **paths, not data** — `inv_db`, `aff_db`, `batches_path`, `db_path` are strings, and
`ensure_batches` already writes the stream to disk rather than shipping it. The doubling lands on
the cheap half. A one-line payload-size assertion at submit, not a design change.

### 3. How much of `_prepare_channel_run` survives

**All of it, as a callee called twice; a thin `_prepare_site_run` composes the two results.** Its
six jobs (`workunits.py:133-466`) — run dir, batch stream, pick cfg/wp, yardsticks, identity, DBs +
run_ids + resume planning — are each genuinely per-channel and stay so under coupling: two run
dirs, two batch streams, two sets of yardsticks, two DBs.

What the site prepare adds is only: the derived crew block resolved once (§6), the two payloads
zipped into one, and the `group_keys` list. That keeps the 330-line function unforked.

**Must NOT be called twice:** `_derive_staffing_for_pair`, already per-pair in `_build_work_units`
(`workunits.py:875`) and correctly above the channel loop.

### 4. The regime filter and the two denominators

**Partition once in `_build_leaf`, into two disjoint order lists; `channel_regime` stays a leaf's
own field.** Each leaf builds its manager from its own partition exactly as today, so `regime_bins`,
`denom` and fill rate (`strategy_runner.py:901-908`, `:1840`, `:1960`) stay per leaf with no new
vocabulary: two leaves, two denominators, the same code against different orders.

**The thing to make impossible is the in-place mutation.** `strategy_runner.py:769-771` does
`inventory.orders = [...]`, which is safe only because the worker owns the object. With two leaves,
the second partition would filter an already-filtered list and **silently produce an empty
channel**. Build both partitions before either manager exists, and assert they sum to the whole
catalogue — this is the same disjointness assertion ticket 01 wants for its `{sku: leaf}` dict, and
the two share one check.

### 5. The three clocks

**`recv_clock` becomes site-wide; `arm_clock` and `put_clock` stay per leaf.**

`recv_clock` (`strategy_runner.py:1219`) exists because the dock's clocks reset every batch while
rows are stamped from an epoch — under one dock there is one such carry, and it lives with the
coordinator that owns the dock. `arm_clock` is the pick crew's, and picking stays per channel by
charter. `put_clock` is the putters' carry and stays per leaf regardless of what ticket 04 decides
about the shared **budget**, because the two leaves' put work is segregated volume.

**What a leaf reports as its span:** not `max(arm_clock)`, and not the calendar. Memory
`calendar-span-is-not-work-days` — a crew share denominated on `_arm_span_days` printed 184%. A
leaf's span is its **distinct work-day count**, and the site's span is the same count, because one
batch is one site day and `staffing.py:723-727` refuses channels whose batch counts differ. The two
leaves and the site therefore share one day count by construction.

### 6. The crew payload

Today `workunits.py:366-369` hands **each** leaf the whole derived site crew, and
`_check_declared_crew` (`strategy_runner.py:541-581`) **enforces** it — it raises unless
`put_crew.size == derived['put']['crew']`. The double count is currently guarded *into place*.

**The coupled payload carries the two site crews once, at unit scope, not per leaf — and the check
moves with them.** `put_crew` and `recv_crew` leave the per-leaf payload and sit beside
`group_keys`; `_check_declared_crew` verifies them **once per unit** against `derived`, and verifies
`k_pickers` **per leaf** against `channel_crew(st, channel=ch)` — pickers really are per channel
(`staffing.py:354-385`).

This is the one place in the map where the fix is **deletion**: per-leaf sizing goes away, and with
it `expected_utilization`'s "single-channel leaves undercut rho" caveat for put and receiving
(`staffing.py:663-667`). Flag-off keeps today's per-leaf payload and today's check untouched —
which is also what keeps the phase-1 asymmetry the map already promises to publish.

### What this ticket hands onward

- **Ticket 03** inherits the config-level finding: the two leaves have no common ancestor below
  `<pair>/`, so a site-scoped artifact cannot live under `<config>/` at all. Noted in its body.
- **The resume guard** graduated out of the map's fog, now that the unit shape is settled:
  [Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md).

## Amendment (2026-09-11, from ticket 04)

**Section 5's "`put_clock` stays per leaf" is superseded: `put_clock` becomes site-wide.**

This answer hedged — "regardless of what ticket 04 decides about the shared **budget**" — on the
assumption that 04 would share a budget. It shares the **clock list** instead
([Design the site put-away pool](04-design-the-site-put-away-pool.md), section 1), and a shared
list cannot carry two epochs: `put_clock` is the absolute carry `crew_start` is measured from
(`strategy_runner.py:1765`, `work_events.put_rows`), so two carries over one list stamp the same
worker's same second at two different absolute instants.

The amendment follows this answer's own reasoning for `recv_clock` — "under one dock there is one
such carry"; under one put crew there is one put carry. Segregated *volume* does not imply
segregated *people*, and the carry belongs to the people.

`arm_clock` is unchanged and stays per leaf. The coupled put base is the **site day start**,
`max(day_start(i), put_clock_site)`; 04 section 9 holds the rejected alternative and the reasoning.
