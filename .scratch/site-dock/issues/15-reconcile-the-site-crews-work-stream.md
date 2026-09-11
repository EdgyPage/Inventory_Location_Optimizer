# Reconcile the site crews' work stream

Type: grilling
Status: open

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
