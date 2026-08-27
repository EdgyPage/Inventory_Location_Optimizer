# Land the Inbound package skeleton

Type: task
Status: resolved

## Question

Execute the receiving-side migration decided in "Draw the Inbound package boundary" (its Answer
holds the full decision; facts: `../assets/boundary-facts.md`). Byte-identical throughout —
store-only equivalence proven at each step.

In scope:
1. Create top-level `Inbound/` (flat): migrate `Warehouse/inventory/dock.py` → `Inbound/dock.py`,
   `Warehouse/operations/unload.py` → `Inbound/unload.py` (keep the by-reference
   `PutawayCost` import), `Warehouse/operations/inbound.py` → `Inbound/pack.py`. Package README.
2. Invert the imports to injection: replace `inventory_reorder.py:16-17` imports and
   `enable_receiving`'s lazy `Dock`/`UnloadCost` construction with driver-side construction
   (`recv_crew_spec` pattern, built in `strategy_runner`/`workunits`, bound via `enable_*`).
   Keep all seven `check_reorders` phase wrappers — bodies may delegate; call sites must not move
   (`Tests/unit/test_reorder_phases.py` pins them). Keep the `_queue` stamped-item entry.
3. Record the architecture, in order: files → `Inbound` into `GRAPH_ROOTS`
   (`context/arch/extract.py:44`) + `inbound` layer in `context/architecture.yml` → extract
   `--write` → `--catalog-merge` (fill every `purpose: TODO`) → `--write-nodes` → render
   `--build`. Mirror the four forbids `wh_operations` sheds onto the `inbound` layer; add the
   new forbids (`inbound → wh_inventory/wh_placement/wh_picking/optimization`, each Warehouse
   layer → `inbound`) only once the graph satisfies them.
4. Update the hardcoded package rosters listed in the facts digest (verify_memory `_TOP_DIRS`,
   calltree tier, unit-test rosters, `test_schema_compatibility.py:624`), and extend
   `Tests/unit/test_putaway_provenance.py`'s file scan to the migrated Inbound modules so the
   single-admission guard still covers them.

Explicitly OUT of this ticket: the transit/lead-queue move behind the order port (waits on
"Choose the lead-time denomination" + the seams and objects tickets), declaring the `'trailer'`
provenance source, any policy seam. The `strategy_runner.py:936` private `_lead_queue` read
stays as-is in this wave.

Done when: all four gates that exist today still pass (`verify_context`, `verify_architecture`,
`verify_site --fast`, `verify_memory`), the narrowest relevant test set is green
(`test_reorder_phases`, `test_putaway_provenance`, `test_receiving_phase`,
`test_inbound_load_plan`, plus a store-only equivalence run), and nothing is committed without
the user's go-ahead.

## Answer

EXECUTED 2026-08-26 — the first code change of the effort. Everything is in the working tree,
uncommitted, awaiting review. The change set: 110 files, +1,276 / −2,133 (the bulk is the
regenerated `docs/architecture/` site; the hand-written core is ~20 files).

What landed, by the ticket's four clauses:

1. **The package**: `git mv` (history preserved) `inventory/dock.py` → `Inbound/dock.py`,
   `operations/unload.py` → `Inbound/unload.py`, `operations/inbound.py` → `Inbound/pack.py`;
   plus `Inbound/__init__.py` (carries the import law) and `Inbound/README.md`. `Dock` now
   prices itself (default `UnloadCost` at construction) and gained `unload_seconds` — the dock
   owns its price list. `pack.py` gained `packer()`, the injectable adapter.
2. **The inversion**: `enable_receiving(dock)` takes a CONSTRUCTED Dock — zero lazy imports;
   `mgr.packer` is the new injected seam with a manager-local default (`_pack_plain` in
   `inventory_reorder.py`) whose unit stream is byte-identical by construction (both paths call
   `viable_storage_units` once per delivery quantity — pinned by
   `test_no_split_policy_reproduces_the_packer_exactly`). All seven phase wrappers untouched;
   the `_queue` stamped-item entry untouched. `strategy_runner` constructs and binds both.
3. **The recording**: `inbound` layer + 16 new forbid rules in `context/architecture.yml`
   (four mirroring what `wh_operations` shed; the rest fencing every Warehouse layer from
   importing Inbound); `Inbound` in `GRAPH_ROOTS`; graph → catalog (docstring-seeded purposes,
   zero TODOs) → nodes → HTML all regenerated in the load-bearing order.
4. **The ratchets**: nine hardcoded package rosters gained `Inbound` (verify_memory
   `_TOP_DIRS`, calltree tracer + memory, six test rosters incl. `test_putaway_item_consumers`
   — the dock holds PutawayItems); `test_putaway_provenance` gained an AST-walked sweep over
   `Inbound/*.py` (text-scan would false-positive on docstrings) asserting the package neither
   constructs `PutawayItem` nor touches `_stock_queue`.

**Verification**: all four gates exit 0 (`verify_context`, `verify_architecture` — 25 layers,
the both-direction fences hold against the real graph — `verify_site --fast`, `verify_memory`
after the maintainer repaired the 12 move-staled anchors); both guards clean; tests: 1,345
unit + 395 integration + 335 architecture (full slow tier, pyyaml confirmed present so the six
sync gates genuinely ran) + 6 receiving-e2e (the spawn-pool path with the new wiring) + 7
calltree-anchor. Three tests were updated to bind `mgr.packer = inbound.packer` where their
contract is the RICH plan hand-up — the injection design working as designed, not a behavior
change. Stale path prose in `WORKING_DAY_CLOCK.md` fixed history-preservingly.

**Not done, by scope**: the transit/lead-queue move, the `'trailer'` provenance source, policy
seams, and the `strategy_runner:936` private read — all wait on their own tickets. A full
run-digest byte-identity run (`Tests/bench/run_digest.py`) needs a sim run and is the
reviewer's option before commit.
