---
name: commit-on-develop
description: "Why day-to-day commits live on develop and main is curated — the reasoning behind the rule in CLAUDE.md"
metadata:
  node_type: memory
  type: feedback
  originSessionId: e57bdfca-d0e1-4d7d-aa03-d855dc2b2802
  modified: 2026-07-29T04:45:06.271Z
---

The develop/main split was adopted 2026-06-22 because the user found `main` **"kind of ugly"** —
granular WIP commits plus huge notebook-output diffs — but wanted to keep `main` as the GitHub
default branch rather than switch it. So `develop` absorbs the noise and `main` gets one clean
"feature landed" entry per milestone via squash-merge.

**Why this is a memory and not just CLAUDE.md:** the *rule* (commit on develop, stage only what
the request touches, don't push unless asked) now lives in CLAUDE.md §5, where every session sees
it. What cannot live in a repo is the **reason** — that this is an aesthetic preference about
history, not a technical constraint. Knowing that tells you how to handle the edge cases the rule
doesn't cover: err toward fewer, cleaner commits on `main`, and never surprise the user by
publishing.

**How to apply:** if a change would make `main`'s history noisy, it belongs on `develop`. If you
are unsure whether something is a milestone, ask rather than squash-merge. Renamed from
`commit-directly-to-main` on 2026-07-28 — the old filename asserted the opposite of its own
content. Related: [[nbstripout-filter]] (the other half of what was making diffs ugly),
[[no-machine-local-paths]].
