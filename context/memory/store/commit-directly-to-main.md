---
name: commit-directly-to-main
description: "Day-to-day commits go on the develop branch (now default); main is curated via squash-merge"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e57bdfca-d0e1-4d7d-aa03-d855dc2b2802
---

As of 2026-06-22 the repo uses a **develop/main split** (main was getting noisy). Commit
day-to-day work **on the `develop` branch** (no feature branches, no PRs unless asked) — same
flat workflow as before, just on `develop` instead of `main`. **`main` stays the GitHub default
branch** (the user kept it that way); it is the **curated** branch, updated only at milestones via
**squash-merge** from `develop` so each main commit is one clean "feature landed" entry — do not
commit directly to `main` anymore unless explicitly told.

**Why:** The user found main "kind of ugly" (granular WIP commits + huge notebook-output diffs)
and wants day-to-day commits on `develop` to keep `main` clean, while leaving `main` as the
default branch. This supersedes the earlier "commit directly to main" rule.

**How to apply:** Stage only the files relevant to the requested change (don't bundle unrelated
config tweaks) and `git commit` on `develop`. Notebook outputs are auto-stripped by an
[[nbstripout-filter]] so notebook diffs stay small. Don't push unless asked.
