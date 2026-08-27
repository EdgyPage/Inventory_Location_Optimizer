# Build the semantic layer and its gates

Type: task
Status: resolved

## Question

Execute the semantic-layer build decided in "Set the column-semantics conventions" and validated
by "Prototype the semantic-layer accessors" (its Answer holds the reaction decisions; the
prototype is `../assets/prototype_semantics.py`, the incident ground `../assets/column-audit.md`).

In scope:
1. The declaration machinery: `Col` (kind/unit/grain mandatory; clock, account, per, null_means,
   logical conditional) + `ByDiscriminator`, declared beside each family's DDL and keyed
   identically to `declared_shape()` (which returns `{'tables': {t: {'columns': [{'name': ...}]}}}`
   — consume it directly). A sibling `*_semantics.py` per family is acceptable if the writer
   module gets heavy.
2. Accessors at BOTH altitudes: frame-boundary guards (sum/ratio/add refusals with incident
   history in the message) where `common/frames.py` hands columns downstream, and
   declaration-time use-assertions extending the `Requires` idea at the `Quantity` layer
   (`core/era.py`'s `QUANTITY_READS` bridge is the pattern).
3. The completeness ratchet in `Tests/architecture/` (the pyyaml-importorskip trap applies —
   keep it stdlib-only so it cannot vanish): declared shape minus tags must be EMPTY per
   covered family; cover sim_db's epicenter tables (`batch_stats`, `carryover`,
   `put_queue_state`, `work_events`) as the proving set.
4. Schema-pipeline discipline throughout (CLAUDE.md §2); `schema-maintainer` reviews any shape
   surface this touches. Tags are metadata — no DDL changes, byte-identical everywhere.

OUT: tagging the remaining families to zero (ticket 12), converting the at-risk readers
(ticket 12), logical renames (ticket 12).

Done when: the ratchet passes on the proving set and fails on a sabotaged missing tag; the
narrowest relevant tests are green; nothing committed without the user's go-ahead.

## Answer

EXECUTED 2026-08-26. What landed:

1. **`Schema/semantics.py`** — the stdlib-only vocabulary and machinery: the nine kinds,
   three clocks, two units of account; `Col` (kind+unit+grain mandatory, conditional axes
   enforced at construction — a time unit without a clock will not even instantiate);
   `ByDiscriminator` for value-dependent kinds; a per-family registry with a COVERED scope;
   guards `sum_of` / `check_add` / `check_ratio` whose refusals carry each column's scar
   note; `validate_uses` (Requires-style readable clauses); `check_completeness` consuming
   `declared_shape()['tables']` verbatim — no parallel column list to drift. Deliberately NO
   unit conversion: `SECONDS_PER_HOUR` stays with its one blessed import chain.
2. **`Optimization/persistence/sim_semantics.py`** — the sim_db family's declarations beside
   its DDL: all four epicenter tables fully tagged (62 columns), every DDL-comment scar now
   a structured `note` (the 101x on `recv_cut` and `cut`, the seven-reason discriminator on
   `carryover.qty` with its two-producers history, class-5 null-meanings, the two id spaces,
   the per-arm axis), and the two logical renames live (`release_day` over `work_day`,
   `frame_index` over `shift_index`).
3. **`Tests/architecture/test_column_semantics.py`** — the completeness ratchet, stdlib-only
   so the pyyaml-importorskip trap cannot vanish it: completeness empty on the proving set,
   sabotage-checked in both directions (removed tag caught, invented tag caught), guards
   proven against the incident ledger, use-assertions catching the receiving-report shape,
   logical names resolving. 10 tests.

Byte-identical trivially: tags are metadata, nothing imports them on any production path,
no DDL or shape id moved (schema pipeline untouched — nothing for `--sync`/`--accept`).
Gates: verify_architecture (2578 nodes, catalog seeded with zero TODOs), verify_site,
verify_context all green; 138 architecture-tier tests pass including the new ratchet.

Unblocks [Run the convention pass to zero remainder](12-run-the-convention-pass.md).
