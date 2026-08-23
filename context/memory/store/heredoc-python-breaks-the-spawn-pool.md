---
name: heredoc-python-breaks-the-spawn-pool
description: "Driving a worker-pool entry point from a `python - <<EOF` heredoc makes every spawned worker die on `<stdin>`, and the pool hangs instead of failing"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T18:59:57.393Z
---

Anything that starts a `ProcessPoolExecutor` must be launched as a real module or script —
`python -m Optimization.analyze_run <root> …`, never `python - <<'PY' … PY`.

**Why:** spawn (not fork) re-imports the parent's `__main__` in each worker. From a heredoc
that path is the literal string `<stdin>`, so every worker dies with
`OSError: [Errno 22] Invalid argument: '…\<stdin>'`. The parent logs
`Running N jobs across the pool…` and then sits there: no figures, no error, no exit. It
reads exactly like a slow run, and cost ~25 minutes before the empty output directory gave
it away. CLAUDE.md §2's "Spawn, not fork" rule covers the picklability half; this is the
other half.

**How to apply:** run pool-driving CLIs as `-m` modules. If a wrapper really is needed,
write it to the scratchpad and run it by path. When a pool looks slow, check for freshly
written output files before waiting — a hung spawn pool and a busy one look identical in
the log.

Related gotcha in the same session: `$COMPARISON_OUTPUT_DIR` is loaded from `.env` by
`Optimization/config/sim_config.py` at import, so it is NOT set in the shell — resolve the
run root with a short Python call and pass it as an argument rather than expanding it in
bash. See [[no-machine-local-paths]] and [[results-drive-location]].
