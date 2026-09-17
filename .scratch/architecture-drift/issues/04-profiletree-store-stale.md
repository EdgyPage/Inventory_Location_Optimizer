# 04 - the committed profiles-tree store is stale against the working tree

Type: bug
Status: resolved

`Tests/architecture/test_profiletree_consumption.py::test_the_committed_store_is_clean_and_current_now`

```
AssertionError: the committed profile-tree store is stale against the working tree - refresh it
  (python -m Schema.profile_tree --write):
    a profiles-tree shape source changed since the store was last synced
```

A profiles-tree shape source moved without the store being refreshed.

**Probably the same fix as 05** - a stale store and a fingerprint disagreement are the two symptoms
the schema pipeline produces when a shape source changes and `--sync` / `--accept` is not run.
Treat them as one job and re-check both afterwards.

The remedy line is in the message, but `CLAUDE.md` section 2 is explicit that schema changes ride
the pipeline and never a consumer edit - `schema-maintainer` owns this.


---

## RESOLVED 2026-09-17  (4582f4a7)

`python -m Schema.profile_tree --write`, but not before checking what it would adopt: the
declared shape id (`build()['schema_id']`) already equalled the stored `head` (`7de9027f83ab`),
so the write refreshed the source fingerprint and adopted no shape. The diff is one line.

That check is the point rather than a formality -- a `--write` on a store that is stale because a
shape GENUINELY moved would adopt the move silently, which is what the pipeline exists to
prevent. This file guesses "probably the same fix as 05"; it was not. 05 was a false-equivalence
bug between two implementations; this was a store nobody re-synced after `ce218f42` (2026-09-16)
touched a shape source.

Gate 6 had therefore been red at HEAD for the whole architecture-deepening effort, which was
verified against nine gates while the sweep looked like ten.
