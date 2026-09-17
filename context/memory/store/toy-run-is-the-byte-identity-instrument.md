---
name: toy-run-is-the-byte-identity-instrument
description: "The tiny smoketest profile plus run_digest is the instrument that can actually fail a refactor; build it before the refactor, not after"
metadata: 
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T08:19:28.902Z
---

A refactor that claims "results unchanged" needs an instrument that can FAIL. The one that
works here is a **toy run digested against a baseline**, and it was built and proved on
2026-09-16 before any structural edit landed:

- `Tests/bench/smoketest.py --profile tiny --stages simulate --workers 12` — ~5 minutes,
  8,000 SKUs, 6 batches, 2 cells, all 34 arms, 136 simulation DBs. It is the smallest run
  that still produces every structural feature (both channels, `_frozen/<pair>/`, the
  what-if levels), so a refactor cannot hide in a level the toy run never builds.
- `Tests/bench/run_digest.py <baseline> <candidate>` — content digests over the whitelisted
  domain tables, floats as exact IEEE bytes, wall-clock columns excluded. Exit 1 on a diff.

**Proved in both directions before being trusted.** `run_digest.py --self-test` plants 1e-9
float damage and the digest catches it; two HEAD-vs-HEAD toy runs came back IDENTICAL across
136 arms and 2,584 table digests. Neither half alone is enough — an instrument that always
says IDENTICAL and an instrument that always says DIFFERENT look the same from one run.

**Why this and not the test suites.** The placement equivalence suites cannot see a ledger
or shared-state change at all — see [[placement-oracles-pin-agreement-not-truth]]. Figures
are not byte-reproducible either ([[figures-are-not-byte-reproducible]]), so a PNG diff
proves nothing. DB rows are the only comparable surface.

**Why:** the moment to build the instrument is before the refactor. Built afterwards it can
only confirm whatever the refactor already did, and there is no clean baseline left to take.

**How to apply:** take the baseline toy run FIRST, on the commit you are about to change.
Digesting against an older baseline makes every intervening commit a candidate explanation
for a diff — usable as a cheap first pass (a green result also clears those commits), but on
a red result take the HEAD~ control run before attributing anything. Runs land under the
`COMPARISON_OUTPUT_DIR` .env key; `run_spec.json`'s `repo_commit` says which commit made one.
