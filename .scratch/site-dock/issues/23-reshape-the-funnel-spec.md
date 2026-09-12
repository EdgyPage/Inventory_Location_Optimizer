# Re-shape the funnel spec for arm pairs

Type: task
Status: open

AFK. **Takeable now** — graduated from the map's "remaining builds" fog by the execution
override (map Notes). [Re-shape the funnel for arm pairs](06-reshape-the-funnel-for-arm-pairs.md)
settled the shape, [Re-shape the selection hand-off](14-reshape-the-selection-handoff.md) built
the HAND-OFF side (an ordered list of rule pairs, and `select()` already refuses a coupled run),
and [Build the coupled work unit](18-build-the-coupled-work-unit.md) built the CONSUMER. What
waits is the spec side, which is now the only half missing. It blocks nothing on this map and
nothing blocks it, so it runs alongside [Build the site space view](20-build-the-site-space-view.md)
and [Build the coupled resume reconciler](22-build-the-coupled-resume-reconciler.md).

## Question

Build 06's spec side: `PHASE2_ARMS` becoming `PHASE2_PAIRS`, `CHANNEL_RESTOCKS` derived from the
rule-pair list rather than declared beside it, the pair-shaped shape refusal in
`_run_whatif_matrix`, and the rewrite of `whatif_config.py:166-170` — whose cross-phase claim
06's own answer makes false, so leaving it is publishing a false statement about the campaign.

Three things later tickets settled that the spec must now reflect:

- **Coupling is DECLARED, not derived from the inbound flag** (`--couple-channels`, 18). Every
  cell couples, including its `inb_off` pole, so the spec must carry the flag on the cell rather
  than inferring it — the charter's original "coupling rides the inbound flag" is unbuildable.
- **`_prepare_site_run` REFUSES more than one config per channel** and refuses ragged arm lists
  (18). The spec is where `CHANNEL_RESTOCKS` is curated, so a spec that produces either is a
  run that dies in the parent rather than a run that silently truncates — worth a spec-side
  check that says so first.
- **Phase 1 is UNCOUPLED and phase 2 is coupled**, so phase 1 ranks placement arms under 2x the
  site put labour ([Build the site put-away pool](19-build-the-site-putaway-pool.md) measured
  it). 06 section 3 says the caveat is a STAMP; this is where the stamp is written.

## What proves it

- **The pair-shaped refusal fails on the defect**, not merely absent from the happy path: an
  arm-shaped `PHASE2_PAIRS` and a ragged pair list both raise, mutation-checked.
- **`CHANNEL_RESTOCKS` derived == `CHANNEL_RESTOCKS` declared today**, asserted, so the
  derivation is proven to reproduce the curated value before it replaces it.
- **No run output moves**: the spec is parsed, not executed, so both preflight canaries and a
  row-level diff against a `git archive HEAD` copy on one uncoupled run.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).
