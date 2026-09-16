# 04 - the committed profiles-tree store is stale against the working tree

Type: bug
Status: needs-triage

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
