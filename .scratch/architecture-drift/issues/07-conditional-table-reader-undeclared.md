# 07 - `_shift_day_select` reads a conditional table without declaring it

Type: bug
Status: needs-triage

`Tests/architecture/test_schema_compatibility.py::test_every_loader_that_reads_a_conditional_table_is_declared`

```
AssertionError: undeclared readers of a conditional table: ['_shift_day_select'];
  declared but no longer reading one: []. A loader that reads a table only some vetted vintages
  have must be listed in `Picking_Data.CONDITIONAL_READS` with the decision about callers that
  cannot negotiate.
```

A loader reads a table that only some vetted vintages carry, and is not listed in
`Picking_Data.CONDITIONAL_READS`.

**The failure mode is silence, not an exception.** A pure column addition still needs a per-vintage
override, or the loader falls back to a dataclass default and a reader sees `0` where the truth is
"not measured" - `free_bins` read 0 rather than None on one vintage until 2026-09-10 for exactly
this reason.

Fix is declaration plus the decision the message asks for: what happens to callers that cannot
negotiate the missing table. `schema-maintainer` owns the pattern
(`docs/design/SCHEMA_COMPATIBILITY.md`).

## Comments

**2026-09-18, triage from the outstanding-work audit.** `_shift_day_select`
(`Optimization/persistence/Picking_Data.py`, three lines) builds a select LIST string; it opens
no connection and reads no table. Its docstring reads "The select list for a `shift_days`
query", and that is what the detector matches: the function-walk in
`Tests/architecture/test_schema_compatibility.py` (near line 349) joins EVERY string constant in
a function, docstring included, and convicts on `'SELECT' in blob.upper() and table in blob`.
The real reader is the named query `shift_day_frame` registered directly beneath the helper,
which the detector's `named` branch already covers. Do NOT add `_shift_day_select` to
`CONDITIONAL_READS` -- declaring a non-reader is a second lie beside the first. Fix the detector
to skip the docstring node (`ast.get_docstring`, or the first `Expr(Constant)` in the body).
