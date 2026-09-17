# 05 - two source-fingerprint implementations disagree on identical input

Type: bug
Status: resolved

`Tests/architecture/test_profiletree_consumption.py::test_source_fingerprint_matches_the_runtree_implementation`

```
AssertionError: the two implementations disagree on identical input:
    profile_tree:        sha256:ee0e55db83539d85936fd2a8b8dd3fb95b57b4763fa7edfa0c20202faff3da0c
    runschema.contract:  sha256:96f1d570624b6e743bc8a520d3e9a9bea1ec3c1053c56d2f8080c338cc0f281c
```

`Schema.profile_tree` and `Optimization.runschema.contract` compute different fingerprints for the
same input. One of them has drifted.

**This is the dangerous one of the seven.** A fingerprint decides whether a run tree is readable and
whether a cached artifact is valid, so two implementations that disagree means one caller population
thinks a tree is current and another thinks it is stale, with no error either way. Fixing a contract
never rescues a finished run - so the longer this sits, the more artifacts are stamped with
whichever answer is wrong.

Likely one job with 04.


---

## RESOLVED 2026-09-17  (207a8d71)

### THE SEVERITY IN THIS FILE IS WRONG, and that is the main finding

This file calls it "the dangerous one of the seven" -- one caller population thinking a tree is
current while another thinks it is stale, with no error either way. Measured before touching
anything: with `SHAPE_SOURCE_DIRS` emptied, the two implementations produce the **same digest**
on the same fixture. The files half was always byte-identical; 100% of the difference was a
second input class that only `runschema.contract` has.

So neither implementation computed a wrong answer for its own inputs. Neither reads the other's
index -- each compares its own fingerprint to its own stored one. And on the real tree they hash
different source LISTS anyway (nine files against ~twenty-five plus a directory), so they were
never going to agree. **The hazard as described needs two readers of ONE index, and there is no
such pair.**

It was a false-equivalence bug: a docstring claiming an identity that stopped being true, and a
test asserting it. Worth fixing -- a copy nothing compares is a copy that drifts, which is the
precedent the test's own docstring cites -- but it was never the emergency this file describes.

### What had actually drifted

`runschema.contract` grew `SHAPE_SOURCE_DIRS` (hashed by name list then content, so an ADDED
file registers in an auto-discovered directory -- a path tuple can only name files someone
already thought of, and `simconfig/configs/` is auto-discovered). Neither copy grew it, and
neither should have: **neither of the other two stores has an auto-discovered input.** The
asymmetry is correct; only the claim of identity was wrong.

### The fix

`Schema/fingerprint.py`: `update_files` (the shared half) and `update_dirs` (contract's alone).
Both fold into a hash the CALLER owns rather than returning a digest -- which is the whole
reason this costs nothing, since `contract` folds files and then directories into one hash and a
digest-returning helper would have changed its output and forced a canary re-prove for a pure
code move.

Neutrality proved, not assumed: `contract` and `store_index` emit byte-identical values.
`profile_tree` moved, because `Schema/profile_tree.py` is one of its OWN shape sources -- so the
algorithm was checked separately by rebuilding against the real source list with HEAD's bytes
for that one file, which reproduces `sha256:9c69435493...`, the exact recorded value.

### The test, restated

It asserted the copies held byte-for-byte, with "these are two distinct objects" as its
non-vacuity check -- which sharing makes exactly backwards. It now pins that there is ONE
implementation, that all three REACH it (a module can import a helper and not call it), and that
the directory half is a declared asymmetry, exercised for what it is for: an added file, a
removal, and a rename with identical bytes.

### Ticket 04

Already fixed by `4582f4a7`, which re-synced the profiles-tree store after checking the shape had
not moved. That commit's note: gate 6 had been red at HEAD since `ce218f42` (2026-09-16), so this
whole architecture-deepening effort was verified against nine gates while the sweep looked like
ten.

### What this unblocks

`architecture-deepening/13`, which says in its own first line: do not start until this ticket has
an owner.
