# placement — the assignment functions

The research subject: given a unit needing a home, which bin does it go in? Everything the
simulator exists to compare lives here.

| Module | Owns |
|---|---|
| `Assignment_Functions.py` | every placement family — the `build_*` factories and the closures they produce |
| `Capacity_Reloader.py` | bounded re-slot via evict-and-requeue |

## The coupling that static analysis cannot see

A strategy stores an **anonymous closure** on `mgr.placement.place_one` / `.place_wave`, so at the
dispatch site in `Warehouse/inventory/Inventory_Management.py` the callee's name never appears. The
edge is therefore invisible to the AST extractor and is declared by hand in
`context/arch/resolver_hints.yml`.

Practical consequence: `inventory/` and `placement/` are coupled more tightly than their imports
suggest. If you move or rename `place_one` / `place_wave` or their factories, update the hints in
the same commit — `extract.py --write` reports unresolved anchors and `verify_architecture` fails,
but only if you look.
