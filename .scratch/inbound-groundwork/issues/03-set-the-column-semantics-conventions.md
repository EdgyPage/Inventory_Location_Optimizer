# Set the column-semantics conventions

Type: grilling
Status: resolved
Blocked by: 04

## Question

Decide the convention vocabulary the coherence pass applies everywhere: the semantic kinds a
column can be (stamp vs span; level vs flow; count vs rate; unit suffixes like `_s`/`_abs`),
the rename rules for existing columns, and what the semantic layer must declare per column.
Grounded in the incident catalog from "Audit the wrong-column incidents". Scope decided while
charting: convention pass AND semantic layer, everything — not just incident families.

## Context update (post "Choose the lead-time denomination")

Two naming collisions now ride the convention pass: `SHIFT_SECONDS` (a reporting frame that
dispatches nothing) vs the new Shift that actually ends work (drain-or-cap, ticket 10); and
minutes-at-the-surface vs seconds-internal — authoring knobs speak minutes, columns stay seconds,
so the convention vocabulary needs an authored-unit vs stored-unit distinction.

## Answer

All five decisions confirmed as recommended, grounded in the incident audit
(`../assets/column-audit.md`):

1. **Kind vocabulary — the audit's nine, adopted as canonical**: STAMP, SPAN, LEVEL, FLOW,
   COUNT (a FLOW whose unit is discrete), RATE, SCORE, SHARE, LABEL/ID. The analysis-end
   `stance` (`level`|`contrast` in `Performance_Evaluations/core/quantities.py`) stays a
   separate figure-side concept.
2. **Tag axes — mandatory everywhere: kind, unit + unit-of-account, grain (per-what).**
   Conditionally mandatory, demanded by the gate when applicable: clock/axis on every time
   column (sim vs wall vs batch-denominated; batch-local vs arm-absolute), null-meaning on
   every nullable column, discriminator wherever kind is value-dependent (`carryover.qty` by
   `reason`), pair links on plan/actual twins, id-space on ids. Nothing optional-and-silent.
3. **Rename policy: logical-layer renames** — the named-query logical vocabulary carries the
   honest name (logical `release_day` over physical `work_day`); physical names stay frozen
   (no vintage churn, no archive rename era); strict naming rules bind FUTURE columns only.
   Physical renames stay available case-by-case where a name is actively dangerous and the
   table is young.
4. **Enforcement: staged** — a completeness gate first (every column tagged, killing
   declaration rot), then use-assertions (`sums`, `ratios-against`, `per`) starting at the
   audit's eight ranked at-risk reads (`Diagnostics/receiving_report.py`, the 101x site,
   first), expanding as reads convert.
5. **Forward naming rules**: unit suffix mandatory (`_s`, `_abs`, `_pct` only 0-100, `_units`
   packs vs `_pieces` merchandise); authored-minutes knobs only in `settings.py` as
   `*_MINUTES`, converted once at the config seam; the CLOCK is always a tag, never inferred
   from a suffix. **`SHIFT_SECONDS` collision resolved**: the reporting frame renames at the
   logical layer (`REPORTING_FRAME_SECONDS` / logical `frame_index`), freeing *shift* for the
   drain-or-cap dispatcher (ticket 10).

The declaration mechanism (where tags physically live, accessor API, gate wiring) is the next
ticket's prototype: [Prototype the semantic-layer accessors](05-prototype-the-semantic-layer-accessors.md),
now unblocked.
