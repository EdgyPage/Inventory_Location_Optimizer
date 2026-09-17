# 13 - three copies of the content-addressed shape store, already drifted

Type: refactor
Status: needs-triage
Blocked by: 11

**Do not start until `.scratch/architecture-drift/issues/05` has an owner.** That ticket is the
fingerprint disagreement -- `Schema.profile_tree` and `Optimization.runschema.contract` compute
different sha256 for identical input -- and its own file calls it "the dangerous one of the seven",
because one caller population thinks a tree is current while another thinks it is stale, with no
error either way. Refactoring the store underneath an unexplained disagreement would bury it.

## Context

Three modules implement the same concept -- immutable content-addressed documents plus a mutable
`INDEX.json` carrying `head` and `source_fingerprint` -- each with a **different subset** of the
same eight operations:

| Operation | `runschema/contract.py` | `Schema/profile_tree.py` | `Schema/store_index.py` |
|---|---|---|---|
| `_sha` / `short_id` / path | `117-133` | `150-160` | -- |
| `_shape_only` / `schema_id` / `build` | `176-234` | `162-205` | -- |
| `load` / `load_all` | `236-263` | `209-234` | -- |
| `read_index` / `write_index` / `head` | `267-289` | `236-257` | `79-104` (no `head`) |
| `source_fingerprint` | `135-174` | `259-276` | `59-77` |
| `adopt` | `370-397` | `278-306` | -- |
| `verify_store` | `415-459` (**4 checks**) | `309-318` (**2 checks**) | -- |
| `diff_shape` | `312-368` | -- | -- |
| `stale_reasons` | -- | `321-338` | `106-125` |

Two more partial copies sit on top: `runschema/hook_check.py:30-55` re-implements `stale_reasons`
inline because `contract.py` has none; `Schema/hook_check.py:30-42` delegates to the other two.

**The copy has already drifted toward weaker invariants.** `contract.verify_store()` checks four
properties -- every document re-hashes to its own `schema_id`, the filename equals
`short(schema_id)`, the head resolves, and every `parent` link resolves with exactly one root.
`profile_tree.verify_store()` checks two. It does not check filenames, and **it does not check the
parent chain even though its own `adopt` writes a `parent` field** (`:298`). The honesty test meant
to keep them aligned -- `Tests/architecture/test_profiletree_consumption.py:286-334` -- pins only
`source_fingerprint`. Nothing pins `verify_store`, `adopt`, `write` or `load_all`.

Also asymmetric: `contract.main --check` prints a `diff_shape` explaining WHAT moved (`:497-500`);
`profile_tree.main --check` prints only that the id moved.

## The layering objection, and why it does not hold

`profile_tree.py:18-28` and `store_index.py:18-26` argue the duplication is deliberate because
`schema -> optimization` is forbidden by `context/architecture.yml` and enforced by a test. **The
constraint is real; the conclusion is backwards.** The shared machinery can live in `Schema/` --
the stdlib-only leaf, which `Picking_Data.py:8-13` already imports from -- and
`runschema/contract.py` imports DOWN. Satisfied by direction, not by copying.

## What to build

`Schema/contractstore.py` holding `ContractStore(tree_dir, shape_of, sources)` with the eight
operations. `runschema/contract.py` and `Schema/profile_tree.py` shrink to their **declarations**
(`SHAPE_SOURCES`, `LEVELS`/`ARTIFACTS`, `_shape_only`) plus an instance; `store_index.py` becomes
the third adapter over the family-keyed variant. Both `hook_check.py` modules call
`store.stale_reasons()`.

**Honour the real half of the objection:** the DECLARATIONS stay separate. An abstraction serving
two contracts' features, levels and artifacts is exactly the coupling both were built to avoid. It
is the store -- a content-addressed JSON directory, identical in all three -- that is shared.

## Verification

- One test suite over `ContractStore` parameterised by adapter, replacing a test that exists only
  because code was copied. It finally covers `adopt`, `verify_store` and the parent chain for the
  profiles tree.
- `verify_store`'s four invariants now apply to all three stores; expect this to surface real
  findings in the profiles tree. Record them rather than fixing them silently.
- Gates 4, 6 (already red on HEAD as drift ticket 04 -- attribute carefully), 10.
