# Build the coupled receiving coordinator and the site recv clock

Type: task
Status: resolved
Blocked by: 20

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
The parallel of [Build the site put-away pool](19-build-the-site-putaway-pool.md), one crew
over: put-away's pool ends the double count for the putters, this ends it for the dock.

**19 handed this the seam it needed.** 02 section 5 decided `recv_clock` becomes site-wide and
18 deferred it, because a site-wide carry over two independent per-leaf resources is a THIRD
model — two full crews serialized as if they were one. 19 resolved the identical question for
put by moving the carry with the resource, and in doing so it split the batch into two halves:
`_build_leaf` returns `replenish(i)` beside `step(i)`, and a work unit runs EVERY leaf's
replenishment before ANY leaf's picks. `replenish` is exactly the window 01's `SITE_PHASES`
interleave needs — phases 0-3 for both leaves, then one shared receive — so the expensive part
of 01's phase order is already paid for.

## Question

Build 01's answer, the coupled half: the `{sku: leaf}` owner dict and the release-seam routing,
the refusal on a non-standing transit, the leaf-accessor refusals, the `SITE_PHASES` interleave
as a SECOND canonical sequence in `Tests/unit/test_reorder_phases.py` (not a new file — that
test's whole value is that every lawful order is written down where a reordering fails), the
coordinator that owns ONE dock across two leaves, and the site-wide `recv_clock` based the way
19 based `put_clock`: at the site day start, `max(day.start_of(i), recv_clock_site)`.

Four things 19 learned that apply here unchanged, and each was a defect before it was a rule:

1. **The reset has one owner.** Whatever the dock resets per batch, a leaf resetting it at its
   own drain zeroes the other leaf's half-spent day with nothing raising.
2. **The uid block clears BOTH channels' pickers**, and the cursor that follows it chains off the
   SITE block's end — 19 found receivers landing inside the putters' block otherwise.
3. **One base, asked for twice.** Both leaves must read the same epoch for the day, so the
   accessor is idempotent per day index and the second caller reads rather than recomputes.
4. **Coupling is an era feature.** 19 refuses a coupled unit with no derived staffing block,
   because the fallback is the double count with a coupled label on it. The same refusal already
   covers this crew (`derived.receiving.crew`).

**Explicitly NOT in scope:** 07's band and the `_site/` artifact declarations with their contract
bump; 15's write-side reconciliation; 05's `SiteGainBundle`.

## What proves it

- **THIS IS THE SECOND COMPARABILITY BREAK**, and like 19's it must be MEASURED per leaf and
  recorded in the answer — `test_a_coupled_unit_matches_the_two_units_it_replaces` is already
  amended to assert the relationship rather than the equality, so extend it rather than rewriting
  it again.
- **Flag-off and uncoupled byte-identical, MEASURED**: both preflight canaries plus a row-level
  diff against a `git archive HEAD` copy. Count the receiving rows before trusting the diff.
- **The one-unload-price question comes due.** The map's fog says 15's `C_store == C_ful` clause
  cannot be written until a coordinator has a price list at all; this ticket gives it one, so the
  answer must say WHICH `UnloadCost` the site dock holds — the two channels run different pick
  configs and today's per-leaf docks price at C = 7.6 s and C = 5.1 s. That is a decision, so if
  it is not obvious once the code is in front of you, graduate it rather than picking one.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).

## Answer

**BUILT** — `Inbound/receiving.SiteReceiving` now serves N leaves over one dock and one
yard: the `{sku: leaf}` owner dict and the two release-seam routes, the non-standing
refusal, the `SITE_PHASES` interleave, the site `recv_clock`, the tagged space-view
contributions and the leaf-accessor refusals. `Warehouse/inventory/` gains the third port
(`owned_skus`) and the site-scope flag those refusals hang on.

### The acceptance is corrected, and the correction is the same shape 18's was

**NO comparability break lands in this commit, and none could have.** The ticket's "What
proves it" says this is the second break and must be measured per leaf. It is not, because
the thing that breaks comparability is a coupled run FIELDING one dock, and a live coupled
standing run is blocked on three things this ticket's own scope list excludes:

- **The site-scoped rows have no home.** A coupled drain's `yard_drains` and
  `yard_trailers` are trailer- and door-denominated: they are the site's, not a channel's.
  Their declared artifact is `<pair>/_site/inbound_<arm-pair>.db` (ADR-0005), and "the
  `_site/` artifact declarations with their contract bump" is the first line of this
  ticket's Explicitly-NOT-in-scope. Writing them to `leaves[0]` in the meantime is
  `a-right-site-total-hides-two-wrong-shares` built deliberately.
- **One transit carries ONE `gain_bundle` slot** for two owners. That is 05's
  `SiteGainBundle` — also on the exclusion list.
- **One dock has one `UnloadCost` and the two channels price at two.** That is the decision
  this ticket was told to take or graduate, and it is graduated (below).

So the coordinator is complete and the run that fields it is 24's, which is this map's own
order everywhere else — 09, 11, 12, 13, 16 and 20 all seated a seam ahead of its consumer,
and 20 said in as many words that the alternative is a composer nobody finds out is wrong.
What replaces the measurement is the same thing 18 substituted: **an uncoupled
standing-yard run pinned row for row**, and a coordinator exercised by two real leaves over
one real yard rather than by a signature.

`test_a_coupled_unit_matches_the_two_units_it_replaces` is therefore **untouched** — it was
amended by 19 and this ticket moves nothing it asserts. Extending it here would have meant
asserting something about a coupled receiving crew that this commit does not field.

### What was built

**1. Two routes home, cross-checked.** Step 1 holds a bare `(sku, qty)` lot, so it resolves
the packer from a `{sku: leaf}` dict built at bind time from each leaf's own catalogue
partition (`owned_skus()`, the third public port — a cross-package caller reaching
`_originals` would be a boundary violation dressed as an underscore). Step 4 holds a
`PutawayItem`, so it asks `regime_of(item.unit)`: nothing is stamped, carried or mapped at
the unit level, exactly as 01 found. The two are **cross-checked at the handoff**, because a
unit whose regime names one leaf while the dict names another is the only shape in which
the catalogue partition and the regime tagging can disagree — and the run it produces has
ledgers that balance in the wrong warehouse.

**2. `SITE_PHASES`, and it is not what 01 drew.** 01 section 4 put phases 0–3 on every leaf
and made only the receive site-scoped. **Phase 1 cannot be a leaf's.** `_advance_lead_queue`
delegates to `transit.advance()`, which decrements the SUPPLIER-lead queue
(`_at_site`), and a coupled site has one yard: two leaves ticking it means an order placed
with a 3-batch lead arrives in 2, silently, on every coupled run with a lead-bearing
catalogue — which is every run since the lead-aware record. So the interleave is
`(leaf, leaf, SITE, leaf, leaf, SITE, leaf)`, and `bind` asserts every leaf holds the
coordinator's own transit, which is what makes "driven on the first leaf" the same call
whichever leaf drives it rather than an arbitrary election.

**3. The interleave is PHASE-MAJOR, and that is load-bearing.** Every leaf runs a phase
before any leaf runs the next. Run it leaf-major — store fires AND releases, then
fulfillment — and `_release_arrivals` departs the open trailer between the two channels'
loads, so **every trailer carries one channel's merchandise**. The charter's mixed load
never happens, the site dock is two docks wearing one name, and no table says so. Verified
by construction in the fixture: one trailer, lots `101 102 201 202`, unloaded across both
leaves.

**4. The site `recv_clock`**, based the way 19 based `put_clock`:
`max(day.start_of(i), recv_clock)`, `open_batch` idempotent per day index (one base, asked
for twice), `note_records(leaf, finish)` committing the carry when the LAST leaf reports.
Never either leaf's `arm_clock` — that is a PICK crew's release instant, and basing the dock
on it would idle the site receivers whenever a pick crew overran its day and misattribute a
picking overrun to the dock's cut. Refuses without a working day, and refuses a day that
opens while a leaf still owes records.

**5. The leaf-accessor refusals, and there are SIX rather than 01's four.** `dock_depth`,
`in_transit_qty`, `transit_snapshot` and `receiving_snapshot` are the ones 01 named. Added:
`drain_receiving_records` and `drain_repack_records`, because those are worse than a wrong
level — the first leaf to call one takes the OTHER channel's rows into its own DB and
restarts the shared crew's clocks half-way through the site's batch, leaving the second leaf
an empty drain and every later row rebased. One `_refuse_site_scope` helper, six callers,
one message shape; the flag is stamped on every bound leaf when the second binds, the first
one retroactively, because scope is a property of the site and not of bind order.

**6. Tagged views.** `_freeze_views` takes the leaf LIST and tags each contribution with the
channel it bound under — never `regime_of` on anything, because the coordinator is the one
object that holds both managers. One leaf stays UNTAGGED, deliberately: a composition of one
partitions nothing, `compose_site_view` returns it by identity, and filtering there would
remove a phantom that belongs to the uncoupled model on every standing-yard run already on
disk. 20 built the refusal that makes the absence unable to survive into the coupled case.

**7. The coupled yard row is parked, not handed down.** `receive` returns the row at one leaf
(where `_receive` appends it, as it always did) and returns **None** with two, parking it on
`site_rows` for `drain_site_rows()`. 24 points that accessor at `_site/`.

### What a review round moved, and the one it had to

The build went through `code-reviewer` before it was committed, and one finding was a
defect rather than a polish note.

**THE RESET LOST ITS OWNER AND NOBODY NOTICED — including the docstring that claimed
otherwise.** Uncoupled, the dock's batch-local crew clocks are restarted by
`Dock.drain_records`, which a leaf reaches through `drain_receiving_records`. This ticket
makes that accessor REFUSE on a coupled leaf (item 5 above) — and appointed no successor,
while `note_records`' own docstring asserted "THE RESET HAS ONE OWNER for exactly the
reason the put pool says so". The put pool's `note_records` ends with
`crew_clock.reset(self.clocks)`; this one did not. The failure is silent and cumulative:
the clocks would carry across site days while the runner kept adding an epoch, so every
row after day 0 would be stamped late by every preceding day's receiving seconds, and
eventually `can_start` would be false from the first unload — the site dock would stop
receiving while `cut` reported a full backlog. `Dock.drain_records`' own docstring is
where that failure mode is written down, which is what makes this worth recording: the
guard removed the owner the docstring was pointing at, and the docstring went on
pointing.

**The general shape is worth keeping:** a refusal that takes a capability away owes the
same commit a replacement, because the refusal itself is what makes the absence
unobservable. Six more findings came out of the same round and all are in:

- the drain never checked `recv_deadline` against the day the clocks are running on, which
  the put pool refuses for a stated reason — now refused here for that same reason;
- `bind` accepted any channel string while `_leaf_for` requires `channel == regime`, so a
  misspelling bound happily and failed on the first unloaded unit, after the doors were
  filled and the crew charged. `REGIMES` already exists; it is checked at bind;
- **three spellings of "is the site real yet"** — `not self._owner`, `len(leaves) == 1`
  and `len(self._leaves) > 1` — one of which made a single BOUND leaf take the coupled
  route and another of which let an unbound coordinator park a row nothing would ever
  write. All three are now the same threshold, the SECOND leaf;
- the epoch check compared floats with `==` (CLAUDE.md section 2) and printed one value
  when one leaf carried `None`. It is a tolerance now, with the `None` case its own
  refusal;
- three refusal messages named a site-scoped accessor that does not exist yet; they say so;
- the owner/regime cross-check ran per UNIT where the fact is per SKU, in a loop inside a
  calltree-anchored file. Memoised per sku, same guarantee.

### Deviations under force

- **`receive(leaf, …)` became `receive(leaves, …)`.** One dock is one drain, so the site
  drain cannot be a per-leaf call that happens to fire twice; and a coordinator serving
  several leaves REFUSES a drain that does not name all of them, which is the only thing
  standing between a coupled leaf's own `check_reorders` and half a site day's receiving
  attributed whole.
- **`SITE_PHASES` lives in `Inbound/receiving.py`, not in the test.** The ticket asked for it
  in `Tests/unit/test_reorder_phases.py`, and the claim is written down there — but as
  `SITE_SCOPED`, declared independently and COMPARED against the constant. The constant has
  to be in the source because `drain` is the thing that has to stay in step with it, and a
  sequence declared only in a test is a sequence the source can drift from. The ratchet is
  three-way and each leg fails for its own reason: `PHASES` pins the names and order,
  `SITE_SCOPED` pins which two are the site's, and a real two-leaf drive pins the phase-major
  expansion against the trace.
- **`Inventory_Manager.site_scoped` is a public attribute set from outside.** The alternative
  — the coordinator counting its own leaves at every accessor — would need the leaf to reach
  the coordinator, which is the import direction the boundaries forbid reasoning about.

### The decision that came due: GRADUATED, not taken

**Which `UnloadCost` the site dock holds is [ticket 27](27-decide-the-site-docks-unload-price.md)**
(`Type: grilling`, `Status: open`). It did not become obvious with the code in front of it;
it became sharper, and it acquired a **third** candidate the map had not considered. The dock's
price is the pickers' price by reference twice over
(`_UnloadCost.from_putaway(_PutawayCost.from_pick(pick_cfg))`), and the two channels run
different pick configs — C = 7.6 s and C = 5.1 s, measured by 17. The three answers are one
channel's price, a units-weighted blend, and **a price LIST keyed by the unloaded unit's own
regime** — which this build makes natural, because `accept` already resolves
`regime_of(item.unit)` at the instant the charge is computed. That third answer makes 15's
`C_store == C_ful` false BY CONSTRUCTION, i.e. it retires the clause the map calls the site
dock's sharpest falsifier. Nothing in the code decides whether that is the better trade.

**The code is shaped so all three stay reachable and none is implied:** `SiteReceiving` is
HANDED a dock, already priced, and never builds or inspects one. Whoever constructs the site
dock (24's driver) applies 27's answer, and no line here has to be deleted for any of them.

### Findings

- **01's "phases 0-3 for both leaves" is wrong for phase 1** — see 2 above. Design-level, and
  only visible from the transit's implementation.
- **Leaf-major loading makes every trailer channel-pure.** The mixed trailer the whole
  charter rests on is a consequence of the loop order, not of the yard.
- **The `receive` refusals had to be re-shaped around a partial drain, not around a flag.**
  There is no "coupled" boolean anywhere in the coordinator: every refusal is about the
  leaves in hand versus the leaves bound, which is a fact rather than a mode.
- **A stale `pytest-of-<user>` tree under the system temp directory wedges the e2e tier,
  and it looks exactly like a hang in the code under test.** After several force-killed
  runs, `Tests/e2e` blocked at ~1% CPU with no child processes, no disk activity and
  `--timeout=420` never firing — for over an hour, on tests that had passed minutes before.
  Removing that directory (14 numbered dirs, ~2k files) returned the tier to normal:
  `test_receiving_e2e` + `test_standing_yard_e2e` then ran 14 tests in 434 s. Recorded
  because the symptom is indistinguishable from a deadlock in the feature, and chasing it
  as one costs hours.
- **Four more test helpers built a coordinator over a non-standing transit** —
  `test_lead_distribution`, `test_space_timeline`, `test_supplier_lead_queue`,
  `test_yard_metrics`, each carrying the same copied comment saying it was "harmless". They
  are now `None` on the v1 path, which is what the production driver does. Six unit failures,
  one cause; worth noting because the comment asserting harmlessness was copied four times
  and none of the copies was true once the refusal existed.

### What proves it

- **Uncoupled is BYTE-IDENTICAL, measured on DB rows.** One standing-yard store arm (20
  batches, 250 SKUs, 3 doors, a 600 s receiving day, split allocation — deliberately a corner
  where the yard BINDS) run in this tree and in a `git archive HEAD | tar -x` copy:
  **149,277 rows across 19 tables identical**, the only masked column being
  `simulation_runs.created` (wall clock). The receiving evidence was COUNTED BEFORE the diff
  was trusted — **76 `receive` rows, 20 `yard_drains`, 22 `yard_trailers`** — because an empty
  table diffs clean. Figures were deliberately NOT diffed (23's finding: a HEAD-vs-HEAD
  control differs on every PNG). Measured TWICE: once on the first build and again on the
  final code after the review round, identical both times.
- `Tests/unit` + `Tests/integration` **2,623 passed, 1 skipped** on the final code.
  **36 new test functions**, across `test_site_receiving.py` (the coupled half, over two
  real managers and one real yard) and `test_reorder_phases.py` (the second canonical
  sequence).
- **`Tests/e2e` green on every file the coordinator touches**, run in pieces because the
  tier wedges on this machine (the finding above): `test_receiving_e2e` +
  `test_standing_yard_e2e` **14 passed in 434 s**, `test_coupled_unit_e2e` all 5 including
  `test_a_coupled_unit_matches_the_two_units_it_replaces` (which this ticket leaves
  untouched), plus `test_coupled_resume_e2e`, `test_channel_runner_smoke` and
  `test_batch_precompute`. **`test_production_hours_e2e` was never reached** — the tier
  wedged before it on every attempt, and it is recorded as not run rather than as passing.
  The receiving/standing pair ran on the code as it stood BEFORE the review round; that
  round added guards only, and the byte-identity run below — which drives the same
  production standing-yard seam — was re-measured on the FINAL code.
- **29 guard mutations, 29 caught.** The non-standing transit accepted; a channel that is
  not a regime; a channel bound twice; a second yard behind one dock; an overlapping
  partition; site scope never stamped; step 1 routed to the first leaf; step 4 routed to the
  first leaf; the cross-check dropped; an unowned sku silently packed; the site yard row
  handed to a leaf; the site route turning on at the FIRST bound leaf; the view left
  untagged; a partial site drain allowed; an unbound coordinator routing two leaves; a drain
  against the wrong day; an unstamped leaf beside a stamped one; two epochs over one set of
  doors; a drain with no leaf; a site drive with no leaf; the site day recomputed per caller;
  the base ignoring the carry; a day opened while records are owed; records reported before
  the day opened; a leaf reporting records twice; the carry committed on the first report;
  **the shared crew clocks never restarted**; the lead queue ticked per leaf; the leaf-scope
  refusal neutered. Each asserted its anchor count before running, so no mutation could be a
  no-op that reads like a passing test (20's `tuple(x)` lesson).
- No `contract.SHAPE_SOURCES` file was touched, so `contract --check` and `preflight --check`
  are green without a canary pair.

### What this hands onward

- **24 wires, it does not design.** Build the `Dock`, the `YardTransit` and the
  `SiteReceiving` at UNIT scope the way `_build_put_pool` builds the put crew; hand the same
  transit to both leaves; `bind` each; chain the receiving uid block off the put pool's block
  end (19's rule 2 — a cursor left at this leaf's own `k_pickers + put_size` puts the smaller
  leaf's receivers inside the putters' block); base the leaves' `_recv_base` on
  `open_batch(day_of(i))` and report each leaf's finish to `note_records`; drive
  `SiteReceiving.drain` instead of two `check_reorders`; drain the dock ONCE and partition its
  records by owner (the six refused accessors are the list of what needs a site-scoped
  replacement); point `drain_site_rows()` at `<pair>/_site/`.
- **Phase 5 already lands after the shared receive** when the drive goes through
  `SiteReceiving.drain`, which routes it to `PutawayPool` exactly as `check_reorders` does.
  `PutawayPool` is untouched by this ticket, and that is only true because the coordinator
  drives phase 5 itself — 24 must not re-route phase 5 back through each leaf's own
  `check_reorders`, or the earlier leaf's put drain runs BEFORE the site's receive and its
  receipts wait a whole batch for a bin.
- **26's `SiteGainBundle` has a concrete need now**: one shared `YardTransit` carries one
  `gain_bundle` slot, so a coupled standing run with a gain policy is unbuildable until it
  lands.
