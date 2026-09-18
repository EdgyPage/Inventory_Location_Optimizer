# 03 - `add_from_bin` acquired a caller and emits no event

Type: decision
Status: needs-triage

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
