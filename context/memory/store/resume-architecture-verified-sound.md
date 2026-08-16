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
