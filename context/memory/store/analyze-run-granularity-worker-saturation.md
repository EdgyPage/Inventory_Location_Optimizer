---
name: analyze-run-granularity-worker-saturation
description: "analyze_run defaults to 4 analysis jobs per cell, so a high --workers count silently idles almost all of them; pass --granularity graph"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5c8daf5-483d-42e5-ab6b-5827d8d48e8f
  modified: 2026-07-30T21:04:41.554Z
---

`Optimization/analyze_run.py` forwards `granularity='config'` by default, which emits only
**4 config jobs + 2 aggregate jobs per cell** (2 pairs × 2 configs, one channel each — *not* 8).
Asking for `--workers 24` therefore leaves ~20 of them idle for the whole pass.

`--granularity graph` fans out to roughly **88 jobs per cell** (4 leaves × ~22 evaluation keys) and
actually saturates a large worker pool.

**Why:** there is no error and no warning — the pass just runs mostly serial and takes far longer
than the worker count suggests it should. It cost a full re-analysis to notice.

**How to apply:** for a whole-sweep re-analysis use
`python -m Optimization.analyze_run <RUN> --workers 24 --granularity graph`. For a single-leaf spot
check the default `config` granularity is fine and starts faster. The flag is a passthrough to
`run_analysis`; it changes only job *partitioning*, never the figures produced.
Related: [[auc-degenerate-on-volume-curves]].
