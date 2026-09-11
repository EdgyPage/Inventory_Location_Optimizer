# Reconcile the site crews' work stream

Type: grilling
Status: resolved

HITL. Skills: `grilling`. Graduated out of
[Re-scope the analysis surfaces to the site](07-rescope-the-analysis-surfaces.md) sections 5(c)
and 7. Takeable now: both parts are decisions, not builds — neither waits on the coupled unit
builder, though both describe code that will be written against it.

Note the boundary this ticket sits on. 07 owns the **analysis** surfaces — what a reader is shown.
This one owns the **write-side reconciliation**: the checks that make the site crews falsifiable
at all. `Diagnostics/receiving_report.py:1-14` states the stake plainly — `work_events` has no
consumer outside `Tests/`, so a work stream can be wired end to end, write duplicated or malformed
rows, and produce a run that looks healthy from every existing angle.

## Question

Two parts, both about a crew that is about to stop being per-channel.

1. **`receiving_report.py` check 3 asserts the uid blocks are disjoint — within one leaf.**
   (`Diagnostics/receiving_report.py:36-43`.) `actor_uid` is the only thing that says WHO did a
   unit of work and nothing enforces it: the DDL has no uniqueness constraint, `Worker` validates
   only non-negativity, and a collision surfaces only as a per-actor rollup quietly merging two
   people — quietest when the crew is small, which is the likely configuration.

   [Design the site put-away pool](04-design-the-site-put-away-pool.md) sets
   `first_uid = max(k_pickers)` precisely so a putter uid means **the same person in both DBs**.
   That is the opposite of disjoint across leaves, and it is deliberate. So decide what the
   check becomes: does it stay per-leaf and gain a site-wide companion that asserts the *same*
   uid in two leaves is the same role; does the site crew get a uid range the check can recognise;
   and what does check 3 mean for `role='receive'` rows once one receiving crew writes into two
   leaf DBs ([Design the site receiving coordinator](01-design-the-site-receiving-coordinator.md))?
   Check 1 (seconds agree) and check 2 (counts agree, exactly) stay per-leaf — `recv_seconds` is
   pack-denominated and stays in the channel's own DB
   ([Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md) §2) — but
   confirm that rather than assume it.

2. **The exact re-pricing self-check is gone, and its loader is orphaned.** Memory
   `equilibrium-check-two-traps` trap 2 describes `reference.recv_exact_check` re-pricing every
   receive row from its SKU and quantity, because a site average was 7x off a real run (51.9 vs
   7.4 s/pack). `Optimization/simconfig/` has no `reference.py`; `unload_price_for` returns zero
   hits repo-wide; `equilibrium.py:78-83` records the retirement of the driver that used it as a
   precondition. `load_receive_events` (`Optimization/persistence/Picking_Data.py:3048`) and the
   `receive_event_frame` query (`:1711`) survive with **zero callers**, and the rows they read are
   documented at `:1705-1709` as existing for exactly this check.

   Decide: does the re-pricing check return at site scope — one crew, two channels' unload costs,
   so the mix argument that made an average wrong is now *stronger* — or does the orphaned loader
   and its query go? A third option is that it returns but somewhere else; note that
   `staffing.py:833-839` already carries a comment correcting a previous stale claim that the
   throughput audit performs this check, so "put it in the audit" has been believed before and was
   not true. `derived.receiving.s_recv` (`staffing.py:840-841`) is REPORTED ONLY and read by
   nothing as of 2026-09-08 — decide whether that stays true.

Memory `hand-run-test-tiers-rot-silently` is the live precedent for part 2: three dead oracles and
a never-executed feature came out of a tier that no gate ran. A loader with no callers is the same
shape.

## Answer

Both parts resolve, and neither the way the ticket framed them. Part 1's cross-leaf check needs no
roster artifact — the role classification is a constant in the scope the check runs in. Part 2's
re-pricing check comes back for far less than the memory implies: the price collapses to a single
constant computable from the sim DB alone, so the orphaned loader gets its caller rather than its
deletion.

### 1. Check 3 is untouched; the new claim is cross-leaf and role-qualified

**Per-leaf, check 3 holds under coupling and gains nothing.**
[Design the site put-away pool](04-design-the-site-put-away-pool.md) starts the put block at
`max(k_store, k_ful)`, so within either leaf the pick / put / receive blocks stay disjoint. The
leaf with fewer pickers gains a gap, which check 3 already declines to police for exactly the
right reason (an allocated-but-idle actor leaves the same gap as a misallocated one).

**What breaks is the global reading, and it breaks by prior decision.** 04 preserves
`actor_uid == picker_id`, so store picker 3 and fulfillment picker 3 both hold uid 3 and are
**different people**; putter uid `max(k)` is deliberately the **same person** in both DBs. So a uid
is not an identity — **(role, uid) is** — and only because `pick` is a per-channel role while
`put` and `receive` are site roles. Global uid uniqueness is off the table: buying it would mean
offsetting the second channel's pickers, which every existing analysis reads as `picker_id`.

**A new site-scope check over the leaf pair, in two clauses:**

- **(i) No uid carries a site role in one leaf and a per-channel role in the other.** This is
  precisely 04's defect: a leaf that builds its own put crew off its own pick cursor puts
  fulfillment's putter at uid `k_ful`, landing *inside* store's picker block. Nothing in the repo
  can see that today.
- **(ii) Every site-role uid observed in either leaf lies at or above both leaves' pick blocks** —
  a floor over `max(max pick uid) + 1` across the pair. Stated as a floor and never as set
  equality: an idle putter writes no rows in one leaf, and set equality would fail a healthy run,
  which is the trap that killed the contiguity check.

Both are computable from the two DBs. **No roster artifact, and no recognisable uid range.** A
magic site offset encodes site-ness in a number nothing validates; (i) and (ii) get the same
guarantee from data already on disk. Which roles are site roles is a constant *in the scope the
check runs in* — the pair-scope check runs only on coupled runs, where `put` and `receive` are site
crews by 04 and [Design the site receiving coordinator](01-design-the-site-receiving-coordinator.md).

### 2. Checks 1 and 2 stay per-leaf — and the build owes the precondition that keeps them honest

Confirmed rather than assumed, and confirming found a hazard the design had not closed. Check 1's
whole justification is that its two surfaces *"share no code below the manager"*. They share an
object:

- `Inventory_Management.py:1199` — `drain_receiving_records()` delegates to `self._dock.drain_records()` (feeds `work_events`)
- `Inventory_Management.py:1208` — `receiving_snapshot()` delegates to `self._dock.snapshot()` (feeds `batch_stats.recv_*`)

Under 01 the `Dock` moves to the coordinator and `mgr._dock` is `None` on both leaves. If the
coordinator hands one leaf the whole drained dock, that leaf's `work_events` **and** its
`batch_stats` both carry both channels' packs, the other reads zero, and **checks 1 and 2 both
PASS** — the site's entire fulfillment receiving labour attributed to store, invisibly. The two
surfaces would agree because they came from one object, which is the definition of a tautology.

**So: checks 1 and 2 stay per-leaf and unedited, and this answer states the precondition.** Each
leaf's `batch_stats` receiving scalars are re-derived from that leaf's **own accepted packs** —
`_recv_seconds`, which 01 section 8 already credits to the owning leaf inside `accept()` — never
from the shared dock's counters. That is a requirement on the coupled build, not a suggestion.
`recv_seconds` is pack-denominated and stays in the channel's own DB
([Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md) section 2),
confirmed against that ticket's own table rather than assumed.

**Plus one genuinely new claim — the site-total closure.** The sum over both leaves of
`work_events` receive seconds equals the coordinator's authoritative site total, which 01 section 8
says it already holds. It is a real check and not a second tautology because the two accumulators
run on different code: the coordinator's total accrues at the dock, `_recv_seconds` accrues at
`accept()` per owner. It is the only check that can see a pack lost or double-counted at site
level.

Moving checks 1 and 2 to site scope was rejected: it discards the per-channel attribution 03 went
to some length to keep.

### 3. The exact re-pricing check returns — as a constant, in `receiving_report.py`

The memory's framing (re-price every row from its SKU and quantity) implies the catalogue and the
run's unload coefficients, neither of which this tool reads. It needs neither. The price collapses:

    unload_cost = per_item + intercept + qty * v_s        (Inbound/unload.py:119, cost_model.py:120)

and `v_s` — the per-unit weight/volume handling term — is **already in the sim DB** as
`sku_scores.handle_var` (`Picking_Data.py:720`), because `UnloadCost.from_putaway` takes its
weight/volume coefficients from `PutawayCost.from_pick`, which carries the pick coefficients
through unchanged (`Order.py:269` computes the stored value from the same `handle_var` on the same
inputs). So every receive row must satisfy

    duration - qty * sku_scores.handle_var(sku)  ==  C

for **one constant C**, from the sim DB alone. No catalogue, no config record, no fit. Because C is
an invariant rather than an average, the pack-mix argument that put `s_recv` 7x off a real run
cannot touch it.

**Option (a): it returns as a new check in `receiving_report.py`**, which finally gives
`load_receive_events` (`Picking_Data.py:3048`) and the `receive_event_frame` query (`:1711`) the
caller they were written for — the query's four columns `(batch_id, sku, qty, duration)` are
exactly its inputs. Deleting them per `hand-run-test-tiers-rot-silently` would have been right if
the check cost what it first appeared to; it does not. "Put it in the throughput audit" was
rejected on the record already (`staffing.py:833-839` corrects a stale claim that it does).

**Its exact form, five decisions:**

1. **Tolerance `_TOL = 1e-6`, reused.** Recomputing re-associates the float, so this is not
   bit-exact; relative error lands around 1e-14 on a ~50 s duration. A second constant would be a
   second thing to justify.
2. **C is fixed by spread, not by a reference.** `max(c) - min(c) <= _TOL` over the rows, with C
   reported. The absolute level needs no oracle; a spread is the failure.
3. **Repack rows are INCLUDED, not filtered.** `_charge_repack` (`Inventory_Management.py:1229`)
   prices a rescue with `dock.unload_seconds` — the same `unload_cost` — so repacks satisfy the
   same C. Including them makes this the only thing in the repo that checks that docstring's
   central claim, *"no new coefficient enters the model"*.
4. **Cross-leaf: `abs(C_store - C_ful) <= _TOL`.** One crew, one price. This is the clause that is
   unstateable today and is the site dock's sharpest falsifier.
5. **Missing scores split two ways.** An empty `sku_scores` makes the check inactive (the idiom the
   tool already uses for a run with no receiving crew); a non-empty table missing a specific
   receive row's SKU is a **FAIL** — a run that unloaded a SKU it never scored is itself the defect.

### 4. `derived.receiving.s_recv` stays reported-only, and stays unwired

Unchanged at `staffing.py:840-841`, read by nothing, keeping its warning. Under coupling the pack
mix it averages spans two channels, so it gets *less* meaningful, not more.

**Explicitly NOT wired to section 3's check.** Making the site average the expectation that
measured C is compared against is the attractive option and the trap: `s_recv` is the script's site
average and C is the crew's exact constant. They are not the same number, and comparing them
rebuilds `a-right-site-total-hides-two-wrong-shares` from scratch. Deleting it was defensible — it
is the only place the site's implied seconds-per-pack is written down at all, which is why it
stays.

### 5. Where the pair-scope checks run, and what an uncoupled run reports

**A sibling `reconcile_pair(store_db, ful_db, site_db)`**, with `main()` grouping
`rt.glob('sim_db')` into pairs when `run_layout.json` carries 03's `coupled` marker. Widening
`reconcile` with an optional peer was rejected: it puts a scope discriminator inside a function
whose contract is "one DB, five checks", and every per-leaf check would grow an
`if peer is not None` branch most runs never take.

**On an uncoupled run there is no pair, so there is no verdict** — `main()` reports
`0 coupled pair(s)` and prints nothing green about a run that has no site. Every published run is
inbound-off, so this is the common case, not the corner. The tool already separates SKIP (wrong
vintage) from PASS-with-`active=False` (no crew ran); this is a third idiom, and it is the one that
keeps the tool from lying by omission across the entire archive.

**An absent site DB on a coupled pair is FAIL, not MISSING.** `MISSING` is the right idiom for
"this arm was not run"; a pair that produced two leaf DBs and no site DB is a run that lost its
site scope, and it must exit non-zero.

### 6. The tool reads a second family, and that is an ordering constraint

`receiving_report.py` opens the site DB alongside the two leaf DBs. `SEMANTIC_USES` grows a second
family key — structurally free, since `Tests/architecture/test_column_semantics.py:186` already
iterates `uses.items()` across families — but **`semantics_for` raises on an unregistered family**,
so 03's site-DB family registration must land before this check can declare its read. Recorded here
so the build does not rediscover it.

The alternative — putting the closure check with the site artifacts and leaving this tool
`sim_db`-only — was rejected: it splits the two halves of one claim ("each leaf's seconds are its
own" / "the two sum to the site's") across two tools nobody runs together.

New declared reads: `sku_scores.sku` and `sku_scores.handle_var`, both `read`. `handle_var` is
tagged `SCORE` (`sim_semantics.py:410`), so a `sum` declaration would be refused by the gate —
correctly, and this check never sums it.

### 7. The site total's grain: per batch

03 already assigns "the receiving crew's denominator" to
`<cell>/<pair>/_site/inbound_<arm-pair>.db`, so the home was settled; the grain was not. **One row
per batch.** A run-total-only check says the site lost 400 seconds and nothing about where; the
drain **is** a batch boundary, which is exactly where the failure lives (check 1's own docstring
names the uncleared checkpoint buffer as the defect it was written for). This is a new declared
table in the site DB and rides the `schema-maintainer` pipeline with the rest of 03's site-DB
build.

## Findings

**Check 5 has a dormant false-FAIL on the stream this ticket is about.** `repack_rows`
(`work_events.py:195`) states its design outright: role is therefore `receive` and only
`event_type` differs. Check 5's SQL is `role IN ('put','receive') AND event_type <> role`. A repack
row is `role='receive'`, `event_type='repack'` — it trips the clause. Every rework row would be
counted a mismatch and the arm would read FAIL. It has never fired because `f_repack` is `assumed`
0.0 (`staffing.py:846`) and ADR-0003's rungs only engage once the free index is dry. It is a check
that goes red on the first honest run of a feature it does not know about, by which point the
reader trusts check 5. Graduated as
[Exempt repack rows and pin the unload constant](17-exempt-repacks-and-pin-the-unload-constant.md).

**`_charge_repack` stops charging under coupling.** It early-returns when `self._dock is None`
(`Inventory_Management.py:1226`), which under 01 is **both leaves**. `_recv_repacks` and
`_recv_repacked_packs` keep incrementing while the seconds vanish — a counter saying work happened
beside a clock saying it cost nothing. Recorded against 01's build, which owns the routing.

**The same silence 14 found, one layer down.** A check can be wired, declared and green while the
thing it claims to verify is unverified. Check 1's "share no code below the manager" was true when
written and would have stopped being true without a single gate noticing.

## What graduates now

**One `task` ticket, takeable immediately:**
[Exempt repack rows and pin the unload constant](17-exempt-repacks-and-pin-the-unload-constant.md)
— the check-5 repack exemption **and** the within-leaf half of the constant-C check. Neither needs
coupling; both are byte-identical outside a crash or a feature whose coefficient is assumed zero;
same shape as 11, 12, 13 and 16, which all went in ahead of what they serve. Pairing them earns
their keep: the check-5 fix alone is three lines nothing exercises, while constant-C is testable
against every inbound-on run already on disk — which is the only way to learn whether it holds
before coupling depends on it.

**Held against the coupled unit builder** (map fog): the two site-scope uid clauses (section 1),
the site-total closure (section 2), constant-C's cross-leaf clause (section 3 item 4), the
pair-scope `reconcile_pair` entry with its `coupled`-marker grouping and FAIL-on-absent-site-DB
(section 5), the second `SEMANTIC_USES` family behind 03's family registration (section 6), and the
per-batch site-total table (section 7).

## Glossary

**Site crew** added to `CONTEXT.md`. The distinction this whole answer leans on — a crew the site
shares against a crew a channel owns — was implied by the **Channel** entry and named nowhere. No
ADR: the identity rule in section 1 is a *consequence* of 04's allocation choice, which is already
on the record, and uids are implementation the glossary must stay clear of.
