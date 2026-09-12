# Build the site put-away pool and the site put clock

Type: task
Status: open
Blocked by: 04, 12, 18

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes),
now that [Build the coupled work unit](18-build-the-coupled-work-unit.md) has made a second
leaf exist. Every seam this needs is seated and exercised by production — `bind_crew(clocks=)`,
`drain_putaway_records(reset_clocks=)`, `_stock(charge_cut=)` and `count_put_cut` (12) — and
the unit that binds them is in (18). No design decision is open:
[Design the site put-away pool](04-design-the-site-put-away-pool.md) settled the rule.

**18 moved one thing INTO this ticket, and said why.** 02 section 5's site-wide `put_clock`
was to land with the coupled unit; it cannot, because `put_clock` is the absolute carry a
BATCH-LOCAL clock list is based from, so a site-wide carry over two per-leaf lists starts leaf
B's putters where leaf A's finished — two full crews serialized as if they were one, which is
a third model and neither of the two on offer. The carry and the shared list are one change.

## Question

Build 04's answer, sections 1 through 8, plus 02's amendment.

1. **The shared list.** One `_Crew(PUT, size=derived.put.crew)` at UNIT scope, one `workers()`
   tuple, both leaves' queues bound to the SAME `list[float]` via `bind_crew(clocks=)`.
   Segregation survives in the queues, not the people.
2. **The uid block** starts at `max(k_store, k_ful)` so it clears both channels' dense picker
   uids. The smaller leaf then has a gap — harmless, and the trade is that a putter's uid means
   the same person in both DBs, which 15's site-role clauses require.
3. **The day's division.** A proportional sub-deadline from
   `derived.put.expected_utilization[channel]` (crew and day cancel, so no new record field),
   then a **residue pass** against the full day. Without it fulfillment absorbs every cut as a
   pure artefact of loop order, and that bias would look exactly like a finding.
4. **The reset.** On a coupled run the manager drain stops resetting the shared clocks; the
   pool resets the one list once per site day, after BOTH leaves have drained. Giving that trap
   a single owner is the main argument for the module.
5. **The site `put_clock`** (02's amendment), based at the site day start:
   `max(day_start(i), put_clock_site)`. 04 section 9 holds the rejected alternative.
6. **`Inbound/putaway_pool.py`** owns the rule, the list and the once-per-day reset; the
   boundaries leave `Inbound/` the only package that may sit above two managers.
7. **Two prices over one crew**, stated and TESTED against the `s_put` ratio.
8. **Two loud refusals:** no working-day grid, and `PUT_QUEUE_SPLIT` (two answers to the same
   question — 12 found this is the same incompatibility stated a third time, because
   `refuse_unpriceable_put` already refuses the split for the era derivation).

**Explicitly NOT in scope:** 01's coupled coordinator and the site-wide `recv_clock` that
depends on it; 07's band reading one site number (04 section 7) and the retirement of the
"single-channel leaves undercut rho" caveat for put, which need the site analysis stage.

## What proves it

- **THIS IS THE COMMIT THAT BREAKS COMPARABILITY**, and a standing test says so before it is
  measured: `Tests/e2e/test_coupled_unit_e2e.py::test_a_coupled_unit_matches_the_two_units_it_replaces`
  pins coupled == uncoupled today and MUST fail here. Do not delete it — amend it to assert the
  new relationship, and **record the measured size of the move per leaf** in the answer. The
  failure names the leaf whose labour moved; that is the measurement.
- **Flag-off and uncoupled are both byte-identical, MEASURED.** Both preflight canaries
  (`tree shape UNCHANGED`), which are uncoupled, plus the unit suite.
- **The residue pass runs in BOTH directions**, proven by a scenario where the STORE leaf
  finishes early — the direction loop order hides.
- **A single-channel catalogue gets share 1.0 and the residue pass is a no-op**, so the
  degenerate case is correct for free (04 section 3). Assert it rather than assuming it.
- **The reset is owned once**: a test that drains leaf A and asserts leaf B's records are still
  on the clocks A left, because the silent zeroing is the failure mode this module exists for.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).
