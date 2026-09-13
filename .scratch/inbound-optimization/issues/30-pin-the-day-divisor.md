# Pin the day divisor to one declaration

Type: task
Status: open

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
