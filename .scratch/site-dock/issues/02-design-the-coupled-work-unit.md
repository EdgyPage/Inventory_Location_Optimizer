# Design the coupled work unit

Type: grilling
Status: open

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
