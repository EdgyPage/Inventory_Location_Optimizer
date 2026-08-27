# Prototype the semantic-layer accessors

Type: prototype
Status: resolved
Blocked by: 03

## Question

How should analysis code read a column through the semantic layer? Build a cheap stub to react
to: where the per-column declaration lives (beside each `Family` DDL? extending `Schema.dataset.bind`?),
what an accessor call site looks like in real analysis code, and what the gate that forbids raw
column reads checks. Must ride the existing schema pipeline (CLAUDE.md §2) — `schema-maintainer`
owns the pattern; `docs/design/SCHEMA_COMPATIBILITY.md` is the constraint set.

## Context update (post "Set the column-semantics conventions")

The conventions are decided — the prototype builds on them, it does not reopen them: nine kinds
(STAMP/SPAN/LEVEL/FLOW/COUNT/RATE/SCORE/SHARE/LABEL); mandatory tags kind+unit+grain with
conditional clock/null-meaning/discriminator/pair/id-space; logical-layer renames (physical
frozen); staged enforcement (completeness gate, then use-assertions at the audit's eight at-risk
reads). Attachment points the audit verified: the DDL-derived shape store, named-query logical
vocabulary, `Requires`, and `Quantity.Source`/`QUANTITY_READS` (the bridge that already derives a
Requires from declared quantities). Ground: `../assets/column-audit.md` sections 2-4.

## Answer

Prototype built, run, and validated by reaction: `../assets/prototype_semantics.py` (runnable,
throwaway; kept as this ticket's asset rather than a throwaway branch — CLAUDE.md's no-branches
rule). Eight demos: FLOW sum allowed; LEVEL sum refused with the 101x history; unit conversion
via declared unit; logical rename resolving; packs+pieces refused; value-dependent kind refusing
row-free aggregation; use-assertions at declaration time; completeness gate against the REAL
`SIM_DB_FAMILY.declared_shape()`.

Decisions from the reaction round:

1. **Declaration home accepted**: a `SEMANTICS` dict of `Col(kind, unit, grain, ...)` +
   `ByDiscriminator` beside each family's DDL, keyed identically to `declared_shape()` so the
   gate is a dict diff. Latitude granted: if the writer module gets heavy, a sibling
   `*_semantics.py` submodule per family is acceptable — decided at implementation, "beside the
   DDL" stays true either way.
2. **Guard altitude: BOTH** — low/mid at the frame boundary (the SemFrame-shaped accessors where
   `frames.py` hands columns downstream) AND high at the `Quantity` layer (declaration-time
   use-assertions). Not either/or.
3. **Refusal messages keep incident history** ("this read published a 101x wrong headline once")
   — valued for future audits.
4. **No remainder**: every column in every family gets tagged; the completeness gate ends EMPTY.
   No staged-by-table carve-out.

Integration facts learned building it: `declared_shape()` returns
`{'tables': {t: {'columns': [{'name': ...}], 'indexes': ..., ...}}}` — the gate consumes it
directly, no parallel list to drift; sim_db alone is 14 tables / ~173 columns of tagging work
(26 done in the prototype's subset).

Graduated: [Build the semantic layer and its gates](11-build-the-semantic-layer.md) and
[Run the convention pass to zero remainder](12-run-the-convention-pass.md).
