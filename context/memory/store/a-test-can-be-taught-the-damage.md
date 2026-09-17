---
name: a-test-can-be-taught-the-damage
description: "a test written AFTER a defect can assert the defect's result as the expected state, which is why nothing ever reports it; two profiles-tree schema documents were deleted by 6adf378e and a test asserted the store should hold exactly one"
metadata: 
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T18:23:48.857Z
---

A test written after a defect can encode the defect's RESULT as the expected state. It then
passes forever, and the gate that should have caught the problem is the thing hiding it.

**The instance.** `6adf378e` ("feat(publish): experiment-publishing pipeline") deleted two
documents from `Schema/schemas/profile_tree/` -- an IMMUTABLE content-addressed store -- leaving
their `INDEX.json` entries behind. Then
`Tests/architecture/test_profiletree_consumption.py::test_the_store_holds_exactly_the_head_document`
asserted `set(docs) == {head}`, with the reason written out: *"exactly the head document (its
whole history is one schema so far) should be committed"*. The history was never one schema.

Two things had to line up for it to stay invisible for weeks, and both are reusable warnings:

1. **The store's own `verify_store()` was a weakened COPY.** `runschema/contract.verify_store()`
   checks four properties; the profiles-tree copy checked two -- no filename check, and no
   parent-chain check *although its own `adopt` writes a `parent` field*. See
   [[symbol-table-relationship-not-verified-by-symbols]] for the neighbouring failure shape.
2. **The test agreed with the damage**, so the one thing that looked at the store from outside
   reported health.

Found 2026-09-17 by ticket 13 collapsing both stores into `Schema/contractstore.py`, which
applied the four checks to both. Both documents were restored from `6adf378e^` after verifying
each re-hashes to its stated id AND its filename under the current projection.

**Why it matters beyond tidiness:** a run validates against its OWN contract
([[verify-tree-uses-the-runs-own-contract]]), so any archived catalogue stamped with a deleted id
could not be validated at all -- `load(sid)` returned `None` and no reader said why.

**How to apply:** when a test asserts an exact SET or COUNT of stored artifacts, check what it
was written against -- `git log` the assertion and the store together. "Its whole history is one
schema so far" is a claim about the past, and a claim about the past in an assertion is a claim
that stops being checked. Prefer an invariant ("every indexed id resolves") over an inventory
("the store holds exactly these"). Related: [[hand-run-test-tiers-rot-silently]],
[[real-test-coverage-is-317]], [[a-count-is-not-a-claim]].
