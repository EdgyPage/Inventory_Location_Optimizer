---
name: nbstripout-filter
description: The nbstripout filter is inactive in a fresh clone, and stripped notebooks carry no rendered outputs
metadata: 
  node_type: memory
  type: project
  originSessionId: e57bdfca-d0e1-4d7d-aa03-d855dc2b2802
  modified: 2026-07-29T04:45:26.563Z
---

Set up 2026-06-22 to stop notebook commits bloating diffs with base64 PNG and widget-JSON output.
The setup step is in CLAUDE.md §3; what a one-line convention cannot carry is the **diagnostic**
and the **tradeoff**.

**Diagnostic:** the filter fails *silently and asymmetrically*. `.gitattributes` is committed but
the filter **command** lives in `.git/config`, which is not — so a fresh clone has the mapping
without the command. If a notebook diff suddenly shows large output blobs, that is the symptom;
run `nbstripout --install`. Nothing warns you.

**Tradeoff, deliberately accepted:** committed `.ipynb` files carry **no rendered outputs**. A
notebook that looks empty or broken on GitHub is not broken — re-run it locally to see the charts.

Related: [[commit-on-develop]] — the same 2026-06-22 push to keep history readable.
