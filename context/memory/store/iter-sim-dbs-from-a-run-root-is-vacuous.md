---
name: iter-sim-dbs-from-a-run-root-is-vacuous
description: "runlayout.iter_sim_dbs walks <pair>/<config>[/<channel>]/ from the directory handed to it -- pass it a CELL dir, never a run root, or it silently finds nothing"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:23.502Z
---

`Optimization/runschema/runlayout.py:iter_sim_dbs(base_dir)` is a structural walk over
`<pair>/<config>[/<channel>]/sim_<strategy>.db` starting from whatever directory it is handed.
Handed a RUN root (which contains one directory per CELL), it mistakes the cell level for the
pair level and, on a mixed catalogue, finds nothing under it -- `scenario._warn_blank_arms(run_root)`
would print "no blanks found" having scanned zero DBs, exactly the false-negative shape in
[[pool-run-swallows-dead-arms]].

**Why:** confirmed reading `scenario._warn_blank_arms`, which is always called with a per-cell
`cell_dir`, never the run root, under the flat work pool ([[flat-work-pool-era]]).

**How to apply:** any caller of `iter_sim_dbs` (or a scan built on it) must iterate cells itself
and call it once per cell directory. See also CLAUDE.md §3 on consuming run-tree levels
positionally -- the same trap, one level higher (cell vs pair).
