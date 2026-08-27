# Build the semantic layer and its gates

Type: task
Status: open

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
