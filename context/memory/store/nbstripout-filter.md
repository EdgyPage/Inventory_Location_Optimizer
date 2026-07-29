---
name: nbstripout-filter
description: Notebooks are output-stripped via an nbstripout git filter; activate it per-clone
metadata: 
  node_type: memory
  type: project
  originSessionId: e57bdfca-d0e1-4d7d-aa03-d855dc2b2802
---

This repo strips Jupyter notebook outputs on commit via an **nbstripout** git filter
(set up 2026-06-22). `.gitattributes` maps `*.ipynb` to `filter=nbstripout`, but the filter
*command* lives in `.git/config` (local, not committed). So in any fresh clone / new environment
the filter is inactive until you run once: `pip install nbstripout && nbstripout --install`.

**Why:** notebook commits were bloating diffs with base64 PNG / widget-JSON outputs; stripping
keeps diffs to real code. Tradeoff: committed `.ipynb`s carry no rendered outputs — re-run to
view charts.

**How to apply:** if a notebook diff shows large output blobs, the filter isn't active — run
`nbstripout --install`. Relates to the [[commit-directly-to-main]] develop-branch workflow.
