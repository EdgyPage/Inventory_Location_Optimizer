---
name: perf-probes-must-be-unpinned
description: "PHASE2_STAFFING_PIN (0ed2dd1582af) refuses the current staffing derivation (2a65ccd5f981): a 400k spec carrying it dies in cell setup ~20 min in; performance probes declare 'unpinned'"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-24T22:22:29.537Z
---

The phase-2 campaign pins its staffing derivation by digest (`PHASE2_STAFFING_PIN`).  The
derivation has moved since phase 1: on 2026-09-24 the pin was `0ed2dd1582af` and the code
derived `2a65ccd5f981`.  Any spec that copies the phase-2 run defaults WITH the pin now
refuses in cell setup, after the whole ~20-minute 400k parent setup has been paid.  Run A of
`.scratch/inbound-fullscale-perf/` died this way once.

The pin is a label digest, not a completion check
([[phases-one-and-two-are-sequencing-independent]]).  A research probe that compares CODE
rather than rankings does not need it.  `_inbound_perf_400k` / `_inbound_perf_400k_sm`
(whatif_config) carry `'unpinned': '<reason>'` instead (commit 75787912).

**How to apply:** a new 400k probe spec states `'unpinned'` with its reason.  A spec that
must reproduce campaign RANKINGS keeps the pin and re-derives it deliberately.  Always bind
the reference catalogue ([[phase2-binds-the-reference-catalogue-not-the-default]]).
