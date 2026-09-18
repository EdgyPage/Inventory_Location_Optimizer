---
name: resume-architecture-verified-sound
description: "Crash-resume was tested end to end on 2026-08-16 and passed — a hard kill mid-flight resumes to full completeness from `--resume DIR` alone"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5eb3d052-8ad3-4cf3-b0d4-c2ec1fa4ece2
  modified: 2026-08-16T06:32:57.311Z
---

The resume path was deliberately tested on **2026-08-16** and the verdict is **sound** — treat it
as trustworthy rather than re-deriving it.

**Method:** a 6-batch two-cell run (272 arms) hard-killed mid-flight with `Stop-Process -Force`
on the whole process tree — a crash/power-loss, not a clean shutdown — then resumed with nothing
typed but `--resume DIR`. Crash state was genuinely messy: 136 sim DBs created, 22 arms mid-flight
with checkpoints on disk, only 1 of 8 groups finalized.

**Result:** 272/272 sim DBs, 8/8 leaves finalized, both cells present, 0 blank DBs, 0 arms with the
wrong batch count, 0 leftover checkpoint pkls, 0 quarantined, 0 tracebacks. Completed work was
skipped (34 arms), mid-flight arms restarted from batch 0, and the finalize gate correctly withheld
partial groups.

**Non-obvious observation worth keeping:** the `strategy-level reset -> batch 0` log line **never
fired**, despite 22 arms being mid-flight. Their groups were never finalized, so no prior run id
existed and `_plan_strategy_start` took the "fresh" branch — same correct batch-0 restart, different
code path, no message. **Do not use that log line as evidence of resume behaviour**; it does not
cover the commonest crash shape.

**How to apply:** on a resume, type nothing but `--resume DIR` — a deliberately-default flag is
misclassified as explicit and loses to the saved spec. Related: [[worker-recycling-pinned-at-one]]
(the same rehearsal proved the pin).

## "SOUND" MEANT COMPLETE, NOT IDENTICAL — corrected 2026-09-18

The 272/272 above is **completeness**: every arm came back. It had been read as safety. It is not
IDENTITY, and nobody had tested identity until it was needed as a precondition for accepting
worker recycling.

Tested: kill a tiny run at 76/136 arms, resume, `run_digest` against a clean run. It came back
complete and it came back **DIFFERENT**, twice over.

1. **The whole warehouse record was written again.** `sim_assets` re-runs pair setup on resume and
   called `save_warehouse_stats` unconditionally — a plain INSERT taking `lastrowid` as a fresh
   `warehouse_id`. `aisle_type_stats` 63 -> 126 rows, `warehouse_stats` 1 -> 2, EXACTLY 2x.
   Anything summing `aisle_type_stats.total_bins` read a warehouse of twice its real size.
2. **A resume erased finished arms' batch counts.** An arm the resume finds already complete runs
   an empty loop (`done = 0`) and `INSERT OR REPLACE` overwrote the true row from the first
   process. `runtime.batches` read 0 instead of 6 on ten arms. Per-arm quantities get normalised
   by `batches`, so a resumed run divided by the wrong denominator.

Both fixed (e617ba67): `save_warehouse_stats` is idempotent on `warehouse_fingerprint`, and an
empty result never replaces a real row. **Every sim DB and the warehouse DB now compare IDENTICAL
across a resume.**

**Residual, and it is not a bug:** 2 of 136 arms still read `batches=0` — arms that FINISHED in a
worker but whose result had not reached the parent when it died. That telemetry was never captured
and cannot be reconstructed. A hard kill costs some telemetry and no simulation output.

**Why:** "it resumed" and "it resumed to the same answer" are different claims, and only the
second one makes resume a safeguard. Related: [[toy-run-is-the-byte-identity-instrument]],
[[pool-run-swallows-dead-arms]].
