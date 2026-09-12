---
name: figures-are-not-byte-reproducible
description: "rendered PNGs differ run-to-run even HEAD vs HEAD (51/51 pixel-differing in a control), so a neutrality check that diffs figure bytes reports a break that is not there — diff DB rows instead"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 68469de0-3988-452f-9eb3-bc8fb5e0c145
  modified: 2026-09-12T02:52:06.552Z
---

**Rendered figures are NOT byte-reproducible run to run.** Measured 2026-09-11 as a HEAD-vs-HEAD
control — the same commit, the same catalogue, the same canary pair, nothing changed — which
produced **51 of 51 PNGs differing**, compared on the IDAT stream rather than on metadata, so
this is not a timestamp in a header.

**Why:** this repo's standard proof that a change is neutral is to run a small sweep in the
working tree and in a `git archive HEAD` copy and diff the outputs. Reach for the figures and the
diff reports a break on every change ever made, including changes that touch nothing. Somebody
will then spend an afternoon looking for it.

**How to apply:** prove neutrality on **DB rows**, not on rendered output — every sim table, row
for row, with the row count checked first (an empty table diffs clean). Two columns legitimately
differ between any two runs and must be stripped before comparing: `simulation_runs`' wall-clock
timestamp, and `runtime_metrics`' wall seconds and peak RSS. `run_layout.json` and
`run_spec.json` likewise carry a `created`/`base` and an interpreter path plus timings. Everything
else is expected to be identical. If a figure genuinely has to be compared, compare the numbers it
was rendered from. Related: [[head-copy-via-git-archive]], [[a-grant-is-not-an-output]],
[[coupled-runs-are-byte-identical-until-the-pool]].
