---
name: stakeholder-site-pilot-frame
description: "The docs site's pitch frame that converged with non-technical readers, and the reader-loop method that found it"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-22T22:21:29.333Z
---

The Experiment 7 buildout (2026-08-16) tuned the site through fresh-context haiku reader
passes in a "busy non-technical ops director" persona. Converged in 3 passes (cap was 4):
10 BLOCKERs → 0, reader restates all three story beats unaided and approves the ask.

**The frame that made would_back flip to yes:** never present the pilot caveat as an
apology. The site's ask is the pilot itself — "the simulation's job was picking which 2
of 34 changes earn a cheap pilot, and what to measure." Supporting beams: (a) plain-word
definitions of FIFO/LPT/Rank_labor *above* the finding callout; (b) "absolute levels are
model-scale, percentage comparisons are exact recomputations (deterministic sim)";
(c) magnitude belongs to the catalogue, the sign never flips (136/136 positive across two
catalogues); (d) a "which warehouse is yours" store-vs-fulfillment table — the last
comprehension gap to fall.

**Why:** future edits to index/home that reintroduce hedging ("these numbers may not
hold...") or drop the pilot-selection frame will re-lose the disinterested reader.

**How to apply:** keep deliberate redundancy across pages (each page is entered directly
from nav and must stand alone) — a bloat-hunting pass flagged it; it is by design. The
reader-loop mechanics (persona prompt, JSONL feedback schema, convergence rubric) are
described in the plan file of the 2026-08-16 session; rebuild them fresh per project
rather than pointing at scratchpad paths.

**2026-08-21 update — Experiment 8 published, the loop is now codified.** Source run
`comparison_whatif_20260820_193432` (the full-scale v2-sampler-era sweep, see
[[v2-sampler-era]]) is the current site experiment; Exp 7 was archived using the same
four-edit pattern as commit `a84fafd` (which itself archived Exp 6). Headline: LPT
+44.8% store / +5.5% fulfillment throughput at ±0.07% labor — replicating Exp 7 almost
exactly across the demand-stream change. New finding this round: the placement podium
*splits* by supply model — rank_cartlabor/rank_labor run ~2.6% better under `lt0`,
map_rank ~0.8% better under `ltrand` — framed on-site as "placement optimization and
supply reliability are complements," not competing levers.

The publish recipe is now a skill, not tribal knowledge: `.claude/skills/publish-experiment/SKILL.md`
holds the whole loop plus its traps. Convergence uses four fresh persona reader agents
per round — `reader-site-director`, `reader-operations-manager` (this pair is the veto:
both must report NO BLOCKING GAPS in the same round to converge), `reader-wms-engineer`,
`reader-warehouse-worker` — spawned fresh each round (never reused mid-loop), capped at
4 rounds. Lessons worth keeping: fresh reviewers *drift* — round 4 surfaced a new demand
class (financial translation of the headline) that three prior rounds had accepted as
hours/percentages; reviewers falsify prose against committed data *including* claims
introduced by earlier fixes — three of round 4's blockers were fix-introduced errors (a
wrong catalogue count, a wrong per-channel-counts claim, and a manifest copied from the
prior experiment instead of the run's own config); the engineer persona's number-audit
passing clean is the signal that the data layer is actually done, not just narratively
plausible.

Owner directive, now enforced: no ad-hoc graphs. Every staged experiment PNG must trace
to a declared producer in `docs/experiments/figures.yml` or a whatif glob;
`context/guards/experiment_guard.py` checks citations-resolve + `schema_id` +
producer-traceability and is wired into the Stop hook, so a new figure only survives if
it rides the registered-evaluation pipeline (meaning future runs regenerate it, not
hand-crop it). New pipeline capacity this round: per-cell rollup CSVs now stage through
the `site_tree` 'cell_data' template plus resolver-accessor ingest — this is the
auditable source behind the labor headline.
