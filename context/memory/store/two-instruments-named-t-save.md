---
name: two-instruments-named-t-save
description: "t_save names TWO instruments -- runtime_metrics' live stopwatch and the calltree's SECTION_MAP attribution, which read 0.000000 for over a month; say which one before quoting a saving number"
metadata:
  node_type: memory
  type: reference
---

`t_save` is **two different instruments measuring the same subject**, and they disagreed by
everything for over a month without anyone noticing:

| | what it is | state |
|---|---|---|
| `runtime_metrics.save_s` | a wall-clock stopwatch in `strategy_runner` (`asm.timers.add('save', ...)`) | ALIVE. Every published saving number is this one: the ~31% section share and the 8.72 -> 4.87 s/arm in [[calltree-framework-first-findings]] round 2 |
| calltree `SECTION_MAP['...'] = 't_save'` | which FUNCTIONS the traced seconds went to | **DEAD from at least 2026-08-18 to 2026-09-17** -- 0.000000 in all six archived captures |

The calltree side died because its eight anchors were the `save_<table>` wrappers, which had
zero production callers: the write path went through `save_checkpoint_bundle`, which was never
anchored at all, and the ninth anchor (`save_worker_checkpoint`) fires only at a checkpoint
boundary a short capture never reaches. Fixed in ticket 07 (323a256b): one anchor,
`CheckpointBuffer._write`, which is now the only place a checkpoint row is inserted. Measured
after the repoint: **0.026052 s** on a 50-row close, against 0.000000 before.

**Why:** a dead instrument is normally loud, because the number it reports is missing. This one
was silent because a LIVE instrument answered to the same name, so "what does t_save say" always
got a plausible answer. The two questions are different -- "how many seconds" versus "whose
seconds" -- and only the first was being asked.

**How to apply:** before quoting or acting on a `t_save` figure, say which instrument produced
it. A per-function drill-down into saving is the calltree's; a section share or a seconds-per-arm
is `runtime_metrics`'. And when two instruments share a name, a zero from one of them is not
self-evidently visible -- check it against the other rather than assuming someone would have
noticed. Related: [[the-instrument-is-what-is-wrong]],
[[symbol-table-relationship-not-verified-by-symbols]],
[[runtime-metrics-is-the-deep-instrument]], [[hand-run-test-tiers-rot-silently]].
