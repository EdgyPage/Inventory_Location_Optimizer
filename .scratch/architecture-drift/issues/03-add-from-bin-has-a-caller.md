# 03 - `add_from_bin` acquired a caller and emits no event

Type: decision
Status: resolved

`Tests/architecture/test_bin_mutation_sites.py::test_the_dead_site_is_still_dead`

```
AssertionError: add_from_bin now has callers: ['Inbound\\trailer.py']
  - it mutates a bin and emits no event, so it needs one
assert not ['Inbound\\trailer.py']
```

The test guards a site that was dead by design: a bin mutation with no corresponding event. It has
a caller now, from `Inbound/trailer.py`.

**This carries a modelling decision, not just a repair.** Either `Inbound/trailer.py` should not be
mutating a bin through this path, or the path must start emitting an event. The repo's conservation
story depends on the answer: `cons_breaks == 0` proves nothing about units lost BEFORE a bin, and
three defects have already hidden in exactly that gap. A silent bin mutation is that gap widening.

Owner: whoever owns the inbound trailer pipeline (`.scratch/inbound-groundwork`, closed
2026-08-27).

## Comments

**2026-09-18, triage from the outstanding-work audit.** The matched lines were read.
`Inbound/trailer.py:30` and `:62` are a module docstring and a method docstring citing
`StorageCart.add_from_bin`'s perfect-packing assumption; there is no call anywhere in the file.
The modelling decision this ticket asks for does not exist. The work is the detector, which
scans text rather than resolving calls: skip docstrings and comments, or find callers through
the AST (`ast.Call` whose func is an `ast.Attribute` named `add_from_bin`). Do NOT add an event
to a path nothing calls.

## Answer

**No caller exists; the detector matched prose.** `Inbound/trailer.py:30` and `:62` are a module
docstring and a method docstring citing `StorageCart.add_from_bin`'s perfect-packing assumption.
Fixed 2026-09-18 by making the scan read code: `_references_add_from_bin` parses each file and
convicts only an `ast.Attribute` or `ast.Name` reference (a call, a bound-method pass, an alias),
never a string constant or a comment, with a non-vacuity test for both directions
(`test_the_caller_scan_reads_code_not_prose`). `test_the_dead_site_is_still_dead` is green on
HEAD with no change to `Inbound/trailer.py`. The modelling decision this ticket asked for was
never on the table.
