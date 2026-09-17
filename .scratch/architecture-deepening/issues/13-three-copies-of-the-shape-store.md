# 13 - three copies of the content-addressed shape store, already drifted

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17  (74f85ac9)

`Schema/contractstore.py`. `contract.py` 500 -> 387 lines; both modules keep their declarations
and delegate the store. The layering objection's conclusion was inverted, exactly as this ticket
said: the shared machinery lives in `Schema/`, the stdlib-only leaf, and `runschema/contract.py`
imports DOWN.

### THE FOUR INVARIANTS FOUND A REAL DEFECT, first try

This ticket predicted it ("expect this to surface real findings in the profiles tree. Record
them rather than fixing them silently"), and it was worse than a nit:

    INDEX lists b001a32ba844 but no document is stored
    INDEX lists e7555f5fa43e but no document is stored

`6adf378e` -- *"feat(publish): experiment-publishing pipeline -- skill, reader panel, guards,
rollup staging"* -- **deleted two documents from an immutable content-addressed store** and left
their INDEX entries behind. A publishing-pipeline commit had no business in the schema store.

Consequences, both silent: the provenance chain could not be walked past the head, and any
archived catalogue stamped with either id could not have its contract resolved (`load(sid)`
returned `None`). Memory `verify-tree-uses-the-runs-own-contract` -- a run validates against its
OWN contract -- so those artifacts were unvalidatable, and no reader would have said so.

Both restored from `6adf378e^`. Before restoring, each was checked to re-hash to its stated id
AND its filename under the CURRENT projection; both did, which is what makes "accidental
deletion" a finding rather than a guess. The chain now walks
`7de9027f83ab <- e7555f5fa43e <- b001a32ba844`.

### A TEST WAS DEFENDING THE DAMAGE

`test_the_store_holds_exactly_the_head_document` asserted `set(docs) == {head}` -- *"exactly the
head document (its whole history is one schema so far) should be committed"*. The store's history
was never one schema. The test was written after the deletion and taught to expect the result of
it, which is why two years of `verify_store` runs and a Stop hook never mentioned it.

It now asserts that every INDEXED id resolves -- the actual invariant for an immutable store,
where an ancestor is not clutter.

### THE SEAM MOVED, and three tests noticed loudly

Redirecting to a throwaway store was `monkeypatch.setattr(mod, '_TREE_DIR', ...)`. That works
only while every operation re-reads the global, and stops the moment one is read at construction
-- **without failing**: the test then runs against the real committed store, and two of these
call `adopt`. `ContractStore.rebased()` makes the redirection an operation, carrying `shape_of`
and `diff` with it.

### TICKET CORRECTION: `store_index.py` is not the third adapter

The ticket reads it as "the third adapter over the family-keyed variant". It is a different
concept:

  * documents live per FAMILY (`shapes/<family>/<short>.json`), not in one flat tree;
  * the index is `{source_fingerprint, families}` -- no `head`, because "which id is current" is
    a question PER FAMILY, and no provenance chain;
  * it has no `adopt`, no `build`, no `schema_id`: it mints nothing. `schema_report --sync` is
    the only writer and arrives holding the ids.

A variant serving it would need a family dimension on every path plus flags disabling head,
adopt, the parent chain and the document hash -- an interface as complex as the implementations
under it, which is the failure this whole effort removes. It shares the fingerprint, which is
what it genuinely has in common; its docstring now says so instead of explaining why it copies.

### Also collapsed

`runschema/hook_check.py` re-implemented `stale_reasons` inline because `contract.py` had none --
a fourth partial copy, one level above the three this ticket names. `contract.stale_reasons()`
exists now and the hook calls it. `Schema/hook_check.py` already delegated.

### Verification

| check | result |
|---|---|
| `Tests/unit` + `Tests/integration` | 3,642 passed / 2 skipped |
| `Tests/unit/test_contractstore.py` | 28 tests, over a throwaway store AND both real adapters |
| `Tests/architecture` | 9 failures, **the same 9 as the HEAD baseline** (measured by stashing) |
| all ten gates | green |

No byte-identity run: this touches no simulation path, and the ticket asks for gates 4, 6 and 10
rather than a digest.

### What this unblocks

Nothing was waiting on 13; it was itself the ticket waiting on `architecture-drift/05`.
