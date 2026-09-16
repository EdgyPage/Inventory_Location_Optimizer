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
