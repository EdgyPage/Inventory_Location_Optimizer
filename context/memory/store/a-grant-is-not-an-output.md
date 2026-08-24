---
name: a-grant-is-not-an-output
description: "the analysis access tally reports INPUTS; a render can be granted everything and still write nothing, so check the [render] run summary line too"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T00:01:17.306Z
---

`run_analysis`'s `[access] run summary: all N evaluation requests granted, 0 denials` is a
statement about **inputs**. It says nothing about whether any figure came out.

`driver._run_one` catches every render exception on purpose — one broken evaluation must
not sink a 21-minute pass — and until 2026-08-23 the only trace was a `ctx.log.error` from
inside a worker whose logger reaches no file. `aggregate/sig.py` called `_boot_ci` without
importing it, so on **every** publish run it raised `NameError`, wrote zero aggregate
significance figures (not even the heatmap), and appeared in no log, beside a summary
saying all 82 requests were granted.

**Why:** the two tallies answer different questions and neither substitutes for the other.
A missing figure with no error looks identical to a figure nobody asked for.

**How to apply:** after any analysis run, read **both** summary lines. `[render] run
summary: no evaluation raised` is the one that means output happened; if it names an
evaluation, that evaluation produced nothing at all. When a declared figure is absent from
a leaf, check that line before assuming the data was missing. See
[[verify-tree-uses-the-runs-own-contract]] for the sibling trap on the contract side.
