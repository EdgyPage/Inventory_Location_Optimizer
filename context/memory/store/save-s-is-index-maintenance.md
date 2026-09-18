---
name: save-s-is-index-maintenance
description: "save_s was index maintenance on keys uncorrelated with insertion order; deferring index creation to run end and dropping three unread indexes cut it 26%, and a big page cache HURTS once the scatter is gone"
metadata: 
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-18T00:43:32.038Z
---

`save_s` was the deep tier's largest growing term and had no owner. Resolved 2026-09-17 across
three commits (`c745de5a`, `e54489bb`, `efc977e3`).

**The mechanism.** A table keyed on `(run_id, batch_id, seq)` only ever ascends, so a checkpoint
APPENDS to the tail. An index keyed on something uncorrelated with insertion order — bin
location, SKU — SCATTERS, so the same checkpoint rewrites pages across the whole index, and that
page count grows with every checkpoint. **The cost of a save was set by how much had been
written so far, not by how much was being written now.** Measured: the last checkpoint of a run
cost **20.27x** the first for identical work; **1.33x** with the scattered index gone.

**The decomposition proves it.** Once `save_s` was split (`sql`/`pkl`/`drn` on the checkpoint
line, with `rows=`), the deep ladder's `t_save` k=1.29 factored EXACTLY: rows per checkpoint
k=0.93 times cost per row k=0.36. All of the superlinearity was the per-row term. SQLite is
99.5% of the section; the pickle checkpoint and the drain are noise.

**What worked, each measured separately against a fixed 12-run baseline (save_s/arm 1.2776,
sd 0.0339, so the bar is ~5.3%):**

| change | save_s/arm | cumulative |
|---|---|---|
| build indexes at RUN END, not per insert | 1.0798 | 0.845x |
| hold ONE connection per arm, not one per flush | 1.0056 | 0.787x |
| drop `ix_bp_bin`, `ix_be_bin`, `ix_picks_run_sku` | 0.9454 | **0.740x** |

**A 256 MiB page cache made it WORSE** — 19.7s against 17.5s alone, 16.6s against 14.9s
alongside the held connection. It had measured 0.61x BEFORE the indexes were deferred, and
almost all of that was absorbing the scatter. With inserts only touching the tail, a large cache
is pure overhead. **Re-earn a lever after changing what it was compensating for; do not inherit
its number.** `synchronous=OFF` bought ~9% and was not taken — durability on a file of record.

**Deferring preserves comparability; deleting does not.** A database indexed at close is the
same artifact — same shape, same stamp, same content — so the saving is entirely in flight and
no consumer can tell. Dropping an index moves the declared id (here d9854632d1b0 ->
4e13ed321df9) and needs a vintage. Keep them as separate commits.

**Why:** an index is a bet — a permanent tax on every insert in exchange for one lookup being
fast. This repo writes ~500 GB per sweep and reads back a fraction, so a wasted index costs far
more here than the same mistake would in a normal application.

**How to apply:** before adding an index, ask whether its sort order matches insertion order; if
not, it will be rewritten in full on every checkpoint. Before keeping one, run `EXPLAIN QUERY
PLAN` over the real read shapes against a real database — a plan NAMING an index is not a query
NEEDING one (`ix_picks_run_sku` was named for a query that never mentions sku). Related:
[[deep-tier-save-knee-and-fixed-arm-cost]], [[two-instruments-named-t-save]],
[[toy-run-noise-floor-is-three-percent]], [[a-count-is-not-a-claim]].
