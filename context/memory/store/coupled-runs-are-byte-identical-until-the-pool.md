---
name: coupled-runs-are-byte-identical-until-the-pool
description: "--couple-channels exists and works but changes no number yet; a coupled-vs-uncoupled comparison showing zero difference is the feature working, not a dead flag"
metadata: 
  node_type: memory
  type: project
  originSessionId: 68469de0-3988-452f-9eb3-bc8fb5e0c145
  modified: 2026-09-11T23:59:04.454Z
---

Since 2026-09-11 (`beda6b77`, site-dock 18) a run can be COUPLED: `--couple-channels` makes one
work unit drive a store leaf and a fulfillment leaf through one batch loop, stamped into the run
tree as `run_layout.json`'s `coupled`. **It is deliberately byte-identical to two uncoupled runs
at that commit, and a test pins it that way** (`Tests/e2e/test_coupled_unit_e2e.py::
test_a_coupled_unit_matches_the_two_units_it_replaces`, row-for-row over batch stats).

So a coupled run that measures identically to an uncoupled one is the feature **working**, not a
flag that silently did nothing. What 18 landed is the unit SHAPE and the deletion of the site
crews from the per-leaf payload; the crews are still FIELDED per leaf, so the labour is unchanged.

**Why the split:** a site-wide `put_clock` over two per-leaf clock lists is not "the double count
persists" — `put_clock` is the absolute carry a batch-local clock list is based from, so one carry
over two lists starts leaf B's putters where leaf A's finished: two full crews serialized as if
they were one. The carry only becomes well-posed with the shared list, so it moved to the pool
ticket. Same argument moves `recv_clock` to the coupled coordinator.

**Why:** someone measuring a coupled run in this window and finding no difference would conclude
the flag is inert and go looking for a wiring bug that is not there — the failure mode
[[fingerprint-chain-verified-end-to-end]] warns about from the other direction.

**How to apply:** before comparing coupled numbers to anything, check whether the
put-away pool module (a `putaway_pool` module under `Inbound/`, site-dock ticket 19)
exists yet. Absent, coupled == uncoupled by construction and the only
differences to look for are structural (one unit instead of two, both leaves finalizing together).
Present, the comparability break has happened — the pinning test fails on the commit that causes
it and names the leaf whose labour moved, so the size of the move is recorded there rather than
re-derived. Related: [[a-grant-is-not-an-output]], [[per-item-charge-hard-break]],
[[derived-fill-is-the-fourth-comparability-break]], [[site-dock-is-shared-across-channels]].
