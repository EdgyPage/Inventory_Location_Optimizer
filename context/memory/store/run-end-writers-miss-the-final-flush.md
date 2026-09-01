---
name: run-end-writers-miss-the-final-flush
description: "strategy_runner's final `if pb:` flush does not fire when n_batches divides the checkpoint cadence, so a run-end writer placed inside it silently writes nothing"
metadata: 
  node_type: memory
  type: project
  originSessionId: a89625a8-4ebf-4e38-b01d-aa171251dac6
  modified: 2026-09-01T01:38:43.880Z
---

`strategy_runner`'s batch loop flushes every `checkpoint` batches and CLEARS its
accumulators, then after the loop does `if pb: save_checkpoint_bundle(...)` for the
leftover window. The cadence is `_checkpoint_every(n) = max(1, int(n * checkpoint_frac))`
with `checkpoint_frac` defaulting to 0.1 — so **whenever `n_batches` is divisible by the
cadence there is no leftover window and that block never runs at all.** At small batch
counts the cadence floors to 1, which makes it never run.

Anything that must be written ONCE AT RUN END therefore cannot ride that block. The yard's
censored-trailer tail (trailers still on site when the run stops) hit this: it is read from
state the per-batch drain never sees, so it can only be written after the loop, and inside
`if pb:` it would have been lost on exactly the round batch counts. It got its own
unconditional writer (`Picking_Data.save_yard_trailers`) instead.

**Why this bites quietly:** the rows it drops are not a random sample. A run-end tail is by
definition whatever the run did not finish, which is the tail of every distribution the run
produced — for the yard it is where an adversarial ordering concentrates its overage, so
losing it would have reported `lifo`'s fee as clipped rather than concentrated and inverted
the arm's signal.

**How to test it:** the e2e harnesses use small `n_batches` (4–6), where the cadence is 1
and the block provably never fires — so an assertion on the run-end rows there is a real
test of the unconditional path, not an accident. Stubbing the writer must make it fail.

Related: [[a-grant-is-not-an-output]] (the same family of silence — the pipeline reports
success while producing nothing), [[coverage-e2e-swallows-worker-logs]].
