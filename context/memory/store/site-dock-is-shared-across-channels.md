---
name: site-dock-is-shared-across-channels
description: "User decision 2026-09-10 — the two channels share ONE dock (mixed trailers, one yard/doors/crew); every per-leaf inbound number is an artefact of the independent-warehouse leaf model"
metadata: 
  node_type: memory
  type: project
  originSessionId: 14ff75d8-c175-4687-8690-27ac35ff9ba2
  modified: 2026-09-11T03:34:52.582Z
---

Decided 2026-09-10 while resolving inbound-optimization ticket 25 ("Decide the contention
regime under the derived crew"): **store and fulfillment trailers are the same trailers.** A
site has one dock; trailers carry both channels' lots; one yard, one door set and one
receiving crew serve both. The user rejected every per-channel framing ("they shouldn't need
separate crews"), including share-slicing the site crew by channel load.

**Why:** the leaf model of 2026-07-02 ([[channel-experiment-independent-warehouses]]) runs
each channel's inbound alone in its own worker while the staffing record derives ONE site
receiving crew and hands it whole to each leaf. So a fulfillment leaf read 0.17 receiving
utilization, a store leaf 0.69, the yard never bound, and "fewer doors" looked like a knob.
It is not: `_unload_split` deals the crew uncapped, so one door with 22 receivers unloads at
the crew's full rate at any door count. The site's own dock binds by itself at the record's
0.85 with no knob search.

**How to apply:**
- Never read a per-leaf yard/receiving/put-away utilization as a fact about the site; it is
  the channel's load against the whole site crew.
- The declared physics: at most TEN receivers support one trailer (additive, even splits,
  steps unmodelled) — knob `INBOUND_DOOR_TEAM`, ticket 28. The crew is not re-derived from it;
  receiver utilization is a reported metric.
- The lead law stays 480 min / 0.7; doors stay 4. Contention is the day cutting under arrival
  burstiness, per site.
- Coupling the channels at the dock is a SUCCESSOR charting effort (seed on the inbound map's
  Out of scope list); the inbound campaign holds behind it. 27 and 28 are the only frontier.
