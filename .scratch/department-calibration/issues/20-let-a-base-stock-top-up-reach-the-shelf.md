# Let a base-stock top-up reach the shelf

Type: grilling
Status: resolved

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

## Answer

**Resolved 2026-09-07 (grilling, three rounds; the user overruled the recommendation on the
rule itself and accepted every downstream recommendation).** Put-away fills an empty bin first
and consolidates into the SKU's own bin only when no empty bin fits.

**Premise corrected first.** "A line served against a remnant misses" is not what the code does:
`drain_sku` (`Warehouse/picking/Workload_Builder.py`) sums a SKU across every bin it holds, so a
line is served whenever total on-hand suffices. Fragmentation fails in two steps instead: every
top-up needs a fresh bin (`_execute_placement` assigns the whole unit and drops the bin from the
free index; `Tests/architecture/test_bin_mutation_sites.py` pins exactly four mutation sites), so
against ticket 23's one-bin-per-SKU sizing the free index runs dry, units fall to `pending` with
no expiry, and stock that is on order never reaches a shelf -- which `_shortfall` records as
`unpicked_unstocked`. The doubled put-away (0.291 vs 0.134) is the doubled placements; the
climbing missed share is the exhausted index. Two facts that shaped the options: the top-up is
order-up-to on position over all bins, so on-hand plus the arriving top-up equals Q, the quantity
the bin was chosen to fit; and a pick's take is bounded above by its plan, so a top-up landing
in a bin can only raise its quantity, never change what a picker takes.

1. **The rule: empty bin first, then the SKU's own bin.** Three shapes were live once options 2
   (take the whole shelf: a 7-unit line picking a 10-unit shelf over-serves demand) and 4
   (accept and stamp: fails the check by construction) fell away:
   **A** a home-bin rule (own bin if the SKU still holds one, else the arm's best free bin);
   **B** empty first, own bin only when no empty bin fits; **C** relocate on top-up (collect the
   remnant, place the whole Q in the arm's best free bin, a priced re-slot move). The
   recommendation was A, on the ground that a base-stock line empties its shelf on more than
   every second line at every rate (P(line >= ceil(mean)) = 0.63 at lambda 1, 0.54 at 10, 0.53
   at 20), so A already relocates a SKU more often than not while keeping the 15% headroom free
   as a candidate set worth ranking; B lets the index collapse to near zero so the arms rank
   whatever just emptied. **The user chose B**: the new-bin decision is exactly where
   optimization occurs, and under A the only optimization moment is a shelf taken to zero;
   empty-first then own-bin achieves consolidation naturally and without bin conflicts. Recorded
   as ADR-0003 with A and C as the rejected alternatives.
2. **The 2026-08-25 no-meeting rule survives as the first rung, not as an invariant.** Put-away
   prefers an empty bin; a putter enters an occupied bin only when no empty bin fits. Nothing in
   the simulator measures the meeting. Memory `empty-bin-preference-is-structural` is amended by
   the build ticket, not before (it is true of the code until then).
3. **No knob.** B replaces the `pending` dead end, so it is a strict no-op in every run whose free
   index never exhausts -- flag-off runs with pallet lots and 15% headroom never do -- and no
   golden moves unless a run was already failing. The user wants the rework NOTICED because it
   may become a knob later: the record (5) is the placeholder, and a future coefficient needs no
   schema move.
4. **The fallback chain and the own-bin choice.** Empty bin -> own bin -> rescues -> pending
   (order (i)); the repack and singleton rescues exist for pallets meeting a small-bin warehouse
   and must not fire for a carton whose SKU has a bin with room. Among several own bins, fill the
   one with the most on-hand first so remnants merge into the fullest bin and the emptiest drains
   and frees; a unit that does not fit whole fills own bins to capacity in that order and sends
   the remainder down the chain.
5. **Inbound does the repacking, and the rework lands in the DBs.** The rescues are silent
   today: no row, no counter, no seconds; a repacked unit inherits `cause='reorder'`. Under B a
   repack is receiving work: the receiving crew's clock is charged per RESULTING pack at the
   dock's own per-pack unload price (`Inbound/unload.py`) when the rescue fires, no travel back
   to the dock, no new coefficient. One new `sim_db` vintage through the pipeline:
   `work_events` gains event type `repack` (role receive, duration, qty, sku, rescued source);
   `bin_placement` gains a column for the bin's state at landing (empty / occupied -- NOT an
   overload of `cause`, which records where the unit came from); `batch_stats` gains three flows
   and a level (top-ups into occupied bins, repack acts, repacked packs, free-index depth at
   batch end). Semantic-layer entries and the six-family classification follow; older vintages
   are served by an override reading the new columns as zero and the bin state as empty, true of
   every pre-B run by construction.
6. **Expectations.** The staffing record stamps an expected repack term of ZERO, provenance
   `assumed`, and the equilibrium audit flags any measured repack against it -- a repack under B
   is a finding about the warehouse's sizing, made loud rather than absorbed into a band. The
   audit reports the own-bin share and the free-index depth per day from `batch_stats`,
   reported not judged, until a run under B shows the steady state. The put-away expectation
   stays one placement per top-up, which B preserves. Ticket 23's fill of 0.85 stays a setup
   fact, never a steady-state target.
7. **Picks drain the smallest-quantity bin first** when a SKU sits in two, ties by location
   order (today: singleton bins, then pallets, each in location order). This is the
   consolidation engine that makes B converge to one bin per SKU; nearest-first was rejected
   because travel is what the arms compete on and a local pick-side optimisation would blur the
   arm's own effect.
8. **Glossary and ADR.** *Base stock* now says where the top-up lands; *Top-up* and *Repack* are
   new entries (root `CONTEXT.md`). `docs/adr/0003-put-away-fills-empty-bins-first.md` records
   the trade-off.

Graduated: [Build the empty-first top-up](24-build-the-empty-first-top-up.md) carries the build
(rule, fallback chain, pick-drain order, the vintage, the receiving charge, the audit columns,
the memory amendment). The 40-day re-read in the fog now waits on 22, 23 and 24.
