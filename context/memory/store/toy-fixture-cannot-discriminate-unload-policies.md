---
name: toy-fixture-cannot-discriminate-unload-policies
description: "Measured 2026-09-19 on _toy_priced: the toy yard holds at most 1 trailer (3 total) against the campaign probe's 24 waiting (642 total, 3,669 staged); the two unload cells tie EXACTLY on pick_owed_s while it separates placement rules by 8.8%. Unload discrimination is a property of a contended yard, not of the metric."
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-22T21:50:54.479Z
---

The toy fixture used for byte-identity checks ([[toy-run-is-the-byte-identity-instrument]]) has
a yard that never contends: at most 1 trailer waiting at a time, 3 total, against the phase-2
campaign probe's 24 trailers waiting (642 total across the campaign; 3,669 staged remainder).
On `_toy_priced`, the two unload cells TIE EXACTLY on `pick_owed_s`
([[pick-owed-s-replaces-flow-totals-for-unload-ranking]]) — while the same metric separates
placement RULES on that fixture by 8.8% (rank_random 157,371 s against fifo 171,202 s).

**Why:** discrimination between unload POLICIES only exists when the yard has more than one
trailer to choose among (see [[unload-key-does-not-rank-like-gain]] and
[[inbound-yard-is-a-stable-queue-under-the-era]] — contention is a property of the catalogue's
arrival rate against the dock count, and the toy fixture's scale never reaches it). The toy
fixture tying is not evidence the metric is broken — it is evidence the fixture cannot pose the
question. Placement-rule discrimination survives on the toy fixture because placement doesn't
need a contended yard to differ.

**How to apply:** never read a toy-fixture tie between unload policies as "the metric can't
rank unloading" — check yard depth first. Use a campaign-scale (>=100k SKU) probe, per
[[unload-key-does-not-rank-like-gain]]'s finding that contention only exists at that scale, to
test unload-policy discrimination. The toy fixture remains the right instrument for placement
rules and for byte-identity, just not for unload-policy ranking. Related:
[[pick-owed-cannot-see-inbound-at-this-demand]] — the campaign-scale probe still ties within
0.1-0.14%, for a different reason (the window is under-demanded, not that the yard never
contends).
