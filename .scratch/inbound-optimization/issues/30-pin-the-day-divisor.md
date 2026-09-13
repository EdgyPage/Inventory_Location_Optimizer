# Pin the day divisor to one declaration

Type: task
Status: resolved

Graduated 2026-09-12 from
[Re-run the gate and fix the fee threshold](29-rerun-the-gate-and-fix-the-threshold.md), which
lost a session to this exact confusion and found the guarantee below unenforced. AFK.

Not blocking phase 1, but it must land BEFORE phase 2: the gate reads the divisor at SIMULATION
time, so a drift here changes arm behaviour, not just a report.

## Question

`settings.py` states a guarantee it does not enforce:

> the SAME threshold feeds the yard fee report -- one knob, two readers, so the gate and the
> metric can never disagree about "overdue"

They can. The knob is one value, but each reader converts seconds to days from its **own**
literal:

| reader | declaration | value |
|---|---|---|
| the fee metric | `Performance_Evaluations/common/units.py:SECONDS_PER_DAY` | `24.0 * SECONDS_PER_HOUR` |
| the urgency gate | `Inbound/gain.py:146` `_SECONDS_PER_DAY` | `86400.0`, a bare literal |
| the gate's test | `Tests/unit/test_gain_plan.py:79` `_DAY` | `86400.0`, a bare literal |

They agree today. Nothing makes them agree, and the test restates the literal too, so it moves
with the gate rather than pinning it — the drift it would need to catch is invisible to it.

**This is the `units.py` failure recurring in a new place.** That module exists because the
hours divisor was spelled inline at five call sites and stayed wrong by 1000x for the life of
the project; its own comment says the divisor is derived from `SECONDS_PER_HOUR` rather than
written as `86400` precisely so there is "one declaration of what a sim second is". The gate
then wrote `86400.0`.

**The duplication is structurally forced, not careless.**
`{forbid: [inbound, evaluations]}` (`context/architecture.yml`) means `Inbound/` may not import
the analysis declaration, and that boundary is correct — receiving must not import analysis. So
"just import it" is not available, and that is why this needs a decision rather than an edit.

### The seam that is available

`Warehouse/kernel/timeline.py` is reachable from both sides: it already declares
`SECONDS_PER_HOUR`, `units.py` already imports it from there ("the one declaration of what the
simulator counts in"), and Inbound is explicitly permitted the Warehouse VALUE layers including
`wh_kernel`. Recommended: declare `SECONDS_PER_DAY` there once and have both readers import it.

That seam also fixes the deeper problem, which is not the duplication but the **invisibility of
the two days**:

- `timeline.DEFAULT_SHIFT_SECONDS` = `8 * SECONDS_PER_HOUR` = **28,800 s**, the site day — a
  batch is one of these under the era.
- `units.SECONDS_PER_DAY` = **86,400 s**, the calendar day — what a carrier's detention accrues
  in, by declaration.

Both are legitimate and the campaign needs both. They live in different modules, and **neither
file names the other**, so nothing on the page warns a reader taking a span in "days" that there
are two answers three-fold apart. Declaring them side by side, each saying what it is for and
naming its twin, is the part that would actually have prevented 29's defect.

### What to decide and do

1. Hoist the calendar-day declaration to `timeline.py` beside the shift, both carrying the
   "which day is this and when do you want the other one" note. Import it in `units.py` (keeping
   its detention rationale as the citing comment) and in `Inbound/gain.py`.
   The alternative, if the hoist is judged to put analysis semantics in the kernel: leave the
   declarations where they are and add a test pinning them equal. Cheaper, catches drift, does
   nothing for the invisibility — say which was chosen and why.
2. Make `Tests/unit/test_gain_plan.py` import the divisor rather than restate it, whichever
   way 1 lands. A test that restates the constant cannot fail when the constant is wrong.
3. Assert the guarantee directly: one test that the gate's "overdue" and `frames._ydf`'s
   `over_threshold` agree on the same span at the same threshold. That is what `settings.py`
   claims, and it is currently claimed by comment alone.

Byte-identity: the values do not change (86,400 stays 86,400), so this must be a strict no-op on
every run. Prove it rather than assume it.

## Answer

**The hoist, option 1 -- and the invisibility, not the duplication, is what earned it.**
`Warehouse/kernel/timeline.py` now declares `SECONDS_PER_DAY` immediately beneath
`DEFAULT_SHIFT_SECONDS`, each carrying the "which day is this and when do you want the
other one" note and naming its twin. `units.py` imports and re-exports it (so
`frames._ydf`'s `units.SECONDS_PER_DAY` and `yard/scorecard`'s import are untouched),
`Inbound/gain.py` imports it and `_SECONDS_PER_DAY` is gone, and
`Tests/unit/test_gain_plan.py` binds `_DAY` to the import instead of restating `86400.0`.

The alternative -- leave the declarations put and add an equality test -- was rejected on
the ticket's own reasoning rather than on cost. It catches drift and does nothing about the
thing that actually produced 29's defect: a reader taking a span "in days" had no way to
learn there were two answers three-fold apart, because neither file named the other. A test
that pins two constants equal is invisible at the point of confusion; two declarations side
by side are not. **The kernel was already the right home and this is not a new
responsibility for it:** it declares `TIME_UNIT`, `SECONDS_PER_HOUR` and -- the point --
already owned the OTHER day as `DEFAULT_SHIFT_SECONDS`. What stays in `units.py` is the
CHOICE of which day a detention accrues in, with its rationale; only the VALUE moved. The
analysis layer and the simulation cannot import each other (`{forbid: [inbound,
evaluations]}`, correctly), so `wh_kernel` is not a convenient seam, it is the ONLY one.

### The guarantee is a test now (section 4b, `test_gain_plan.py`)

`settings.py`'s "one knob, two readers ... can never disagree about overdue" is asserted by
handing the SAME span to both readers at the same threshold: the gate through the real
`gain_gated` entry (two identical loads, so the plan ties and keeps the handed order --
the aged trailer leads the output if and only if it was forced into the urgent prefix), the
fee through the real `frames._ydf`. Neither side restates the predicate; a test that
recomputes what it is checking cannot fail when the thing is wrong, which is precisely how
the old `_DAY = 86400.0` managed to move WITH the gate.

**Two findings came out of writing it.**

1. **The two readers split the boundary instant, and it is now pinned rather than
   smoothed.** The gate is `>= threshold` (already overdue jumps the plan); the fee is
   `overage_days > 0`, i.e. strictly `> threshold` (a trailer at exactly the free allowance
   has accrued nothing). Both are right for their own job. It is NOT fixed here: changing
   the gate would alter arm behaviour, and this ticket is a declared no-op. It is
   unreachable in practice for a reason worth recording once instead of re-deriving -- the
   two never measure the same span at all (the gate measures `frozen_at - arrived` at a
   drain, on a trailer still standing; the fee measures `emptied - arrived`, the whole
   detention), so a per-trailer agreement is not a thing that exists. Only the CONVERSION
   can agree, and that is what is pinned. `test_the_two_readers_split_the_boundary_instant_
   and_that_is_recorded` fails if the boundary ever moves.

2. **The span set has to sit inside the 3x band or the agreement test is vacuous.** A
   0.5-day span is NOT sensitive to the site-vs-calendar drift: 0.5 x 3 is 1.5, still under
   a 2.0-day threshold, so both readers keep saying "not overdue" and the test passes
   through the exact defect it exists to catch. 1.0 and 1.5 days carry it -- under the
   threshold on a calendar day, over it on the site day -- and
   `test_a_drifted_divisor_breaks_the_correspondence` monkeypatches the gate to the site day
   and asserts *those two specifically* flip, so the span set cannot be trimmed back into
   vacuity later without a failure.

### The ratchet, and the gap in the one that already existed

`Tests/unit/test_timeline.py` already ratcheted the HOURS divisor (`test_no_module_carries_
its_own_copy_of_the_divisor`, the five-copies-wrong-by-1000x guard). The day now has the
same treatment in `test_no_module_declares_its_own_seconds_per_day`: two offences, an
assignment to `SECONDS_PER_DAY` anywhere but `timeline`, and an `86400` literal in code
(AST-unparsed, so prose quoting the number to explain this history stays legal -- the same
rule the hours ratchet settled on). Proven to fail by reintroducing the literal in
`Inbound/gain.py`.

**It scans `Inbound/` as well as `Optimization/` and `Warehouse/`. The hours ratchet does
not, and never did** -- so the gate's module was outside the one guard that would have
caught this class of defect, which is a structural reason the day divisor drifted there
rather than bad luck. Also fixed: `test_the_analysis_layer_divides_by_the_kernels_own_
declaration` asserted the exact source LINE `from Warehouse.kernel.timeline import
SECONDS_PER_HOUR` and broke the moment a second name joined it. It matches through the AST
now -- pinning an import's spelling breaks on every legitimate edit while catching no
drift.

### Byte-identity, proven

The derived expression and the retired literal are the same DOUBLE, not merely equal:
`24.0 * 3600.0` and `86400.0` both pack to `40f5180000000000`. Beyond the value,
`test_both_readers_resolve_to_the_one_kernel_declaration` asserts `is`-identity across
`gain`, `units` and `timeline` -- equality would pass on the state this ticket found, where
two modules happened to agree and nothing held them.

Green: unit tier **2393**, `test_standing_yard_e2e` + `test_receiving_e2e` **14**, and the
gates `verify_context`, `runschema contract`, `profile_tree`, `path_guard`, `docref_guard`.

Everything else was measured against a STASHED baseline rather than assumed, and three
things already failed before this change: `preflight --check` (the run-tree fingerprint),
the memory mirror, and `verify_architecture` at two problems -- a stale `graph.json` and an
uncatalogued `Tests/unit/test_funnel_window.py`. `verify_architecture` is back at exactly
those two; it briefly had a third, the `SECONDS_PER_DAY` catalog anchor, fixed below.

`Tests/architecture` is **14 failures before and 14 after**, and the sets differ by one
member SWAPPED WITHIN the same family: `test_committed_graph_is_current` (baseline) for
`test_architecture_html.py::test_currency_manifest_matches_fresh_build` (after). Both are
stale-derived-layer checks -- moving a symbol between two catalogued files changes what a
fresh site build emits for those pages -- so this is the owed arch regeneration surfacing
under a different name, not a new defect. The rest of that tier's failures are the same
family plus the known-unrelated `test_the_dead_site_is_still_dead` (spun off at 16).
Regenerating the layer HERE was declined deliberately: it is stale from five prior tickets
(09/13/15/21/28), so running the chain now would sweep their drift into this commit, which
CLAUDE.md section 5 forbids.

### Also touched

- `context/files.yml`: the `SECONDS_PER_DAY` anchor moved from `units.py` to `timeline.py`,
  following the declaration. Without this the catalog verifier gained a third problem.
- `settings.py`: the guarantee now cites its enforcement instead of asserting it, and says
  outright that days there are CALENDAR days.
- `whatif_config.py`: its `PHASE2_THRESHOLD_DAYS` note cited `Inbound/gain.py:_SECONDS_PER_
  DAY`, a constant that no longer exists; it names the kernel declaration both readers now
  import.

**Owed:** the derived arch layer, as at 09/13/15/21/28. No schema event -- no DDL, no
run-tree shape, and `preflight` reports the same fingerprint state as before the change.
