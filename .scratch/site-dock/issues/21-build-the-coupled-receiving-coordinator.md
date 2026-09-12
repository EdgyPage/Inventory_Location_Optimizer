# Build the coupled receiving coordinator and the site recv clock

Type: task
Status: open
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
