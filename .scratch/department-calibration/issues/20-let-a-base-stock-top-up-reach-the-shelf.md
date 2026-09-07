# Let a base-stock top-up reach the shelf

Type: grilling
Status: open

Graduated from [Build the line floor](17-build-the-line-floor.md), whose 40-day check found the
store section -- fielded at its floor -- degrading anyway. HITL: a placement-rule decision.
Skills: `grilling` + `domain-modeling`; memory `empty-bin-preference-is-structural` is the
prior decision this one meets.

## Question

Put-away never adds to an occupied bin: `Inventory_Manager._execute_placement` removes a
chosen bin from the free index, so an occupied bin is not a candidate (memory
`empty-bin-preference-is-structural`, measured zero occupied-bin placements across four arms
on 2026-08-25 -- under levels of ~5 lines per SKU, where a reorder lot always wanted a fresh
bin anyway). Under BASE STOCK every reorder is a top-up of exactly what one line took, so a
line that takes PART of a shelf leaves a remnant in the SKU's bin and the top-up opens a SECOND
bin. On the reference pair's store section, 215,358 of 239,938 SKUs held more than one bin by
day 40 (fielded at the floor: Q/L 1.01); a line served against a remnant misses; the missed
share climbed 0.127 -> 0.283 across the window against a stamped expectation of 0.095, 35 of
40 days ended capped (36 with overtime), and put-away read 0.291 against an expected 0.134 --
more placements, each a fresh bin. Base stock as decided ("Choose the coverage floor",
decision 1: "every pick reorders what it took") assumed the top-up lands back on the shelf.

Decide how a base-stock top-up reaches the shelf:

1. **Top up the SKU's own bin** -- a bin holding the same SKU with free capacity is a
   candidate for that SKU's put-away (and preferred). Re-opens the 2026-08-25 decision, which
   ruled out putter/picker meetings in one bin during put-away; a same-SKU top-up is exactly
   such a meeting.
2. **Take the whole shelf** -- a line that reaches a base-stock shelf takes ALL of it (the
   remainder is the shortfall, served after the restock), so the bin empties, returns to the
   free index, and the top-up lands in a free bin as today. Changes what a pick takes, not
   where a put goes; the shortfall reporting already exists.
3. **Consolidate on the way in** -- the reloader / repack seam merges a SKU's fragments when
   a top-up arrives. Adds a labour term nobody declared.
4. **Accept fragmentation and stamp it** -- the fill rate's premise (a shelf at `Q_s` when
   the line arrives) is wrong under fragmentation; record the expected steady-state
   fragmentation instead. Leaves the store section's missed share trending, which fails the
   equilibrium check by construction.

Done when: the rule is decided and recorded, a task ticket carries the build, and the glossary's
*Base stock* entry says where the top-up lands.
