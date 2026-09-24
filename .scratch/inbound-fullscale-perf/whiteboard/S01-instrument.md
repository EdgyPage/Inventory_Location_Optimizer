# S01 -- I0: inbound performance attached to every run

## Question

The inbound path (receive, gain planning, unload, put-away) was one stopwatch, `reord_s`.
Can every run carry its own carve -- which part of the reorder phase cost the time, how
deep the yard stood, how much of the planner's ranking was used -- without moving a
number?

## Prediction (registered in the plan)

Toy digests IDENTICAL; overhead < 0.5% of `reord_s`.

## What was built

* `Warehouse/kernel/perf_probe.py` -- a process-global stopwatch (a worker runs one unit;
  the kernel is importable from every layer, and `Inbound` may not import `Optimization`).
  Sites: `SiteReceiving.drain` (phases 0-3 -> `inb_pre`, phase 5 -> `put`),
  `SiteReceiving.receive` (`inb_freeze`, `inb_pack`, `inb_unload` NET of any yard ranking
  inside it, `inb_handoff`; counters `inb_drains`, `yard_T_sum`, `yard_T_max`,
  `yard_pulls`), `YardTransit.yard_order` / `dock_order` (`inb_yplan` / `inb_dplan`),
  `plan_order` both paths (`plan_rounds`, `plan_places` = 2 x remaining per round),
  `IM._stock_ranked` (`put_open`, `put_opens`, `put_units`), and the uncoupled
  `check_reorders` (`inb_pre`, `put`).
* `strategy_runner`: the probe is drained EMPTY at the loop start (setup places initial
  stock through the same pools), then after every site drive -- charged to every leaf in
  full, the `reord_s` convention -- and after an uncoupled leaf's `check_reorders`, through
  `_charge_probe` (literal `timers.add('inb_...')` lines: the anchor gate finds a section by
  name).  `startup_s` (worker entry -> this leaf's loop clock) and `sib_setup_s` (the
  sibling's setup inside this leaf's `total_s`) are stamped on each leaf's result.
* `runtime_metrics`: 9 overlay spans (`label=None`, NULL-able), 8 counters, 2 setup
  columns; `_migrate_setup_columns` now runs in `record_arm` so a resume into an older
  root widens the table instead of losing the row; schema `cd91e75b27ba` ->
  `1cb12df3991f` through `scripts/schema_report.py --sync` / `--accept`.
* `run_digest`: the new SECONDS and the two planner WORK counters (`plan_rounds`,
  `plan_places` -- what an exact optimisation removes) are excluded like every wall-clock
  column; the decision-driven counters stay hashed.

## Measurement

* `_toy_priced` (`comparison_whatif_20260924_155253`) against the pre-change toy
  `_135948`, and `_toy_merge` (`_155600`) against `_134228`: **IDENTICAL on every cell**
  (`run_digest --cell`, six cells; the run-scope runtime DB is left out by `--cell`, its
  shape moved on purpose).
* The invariant: **0 of 88 rows** have the inbound overlays summing past `reord_s`.
* The setup columns read the build order exactly: the store leaf (built first) carries
  `sib_setup_s` = 3.6 s = the fulfillment leaf's build, inside its own `total_s`.
* `Tests/unit/test_perf_probe.py` (3): accumulate/drain, a real standing-yard drain charges
  every receive sub-span and counts its drain, the probe moves no number.

## Residual

Overhead: the toy's `reord_s` is 0.13-0.41 s per arm, below the 3.1% toy noise floor's
resolution; the probe is ~a dozen `perf_counter` reads per drain.  Run A prices it at scale.

## Next

S02: run A at 400k, 20 batches.
