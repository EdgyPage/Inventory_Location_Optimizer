# Run the convention pass to zero remainder

Type: task
Status: open
Blocked by: 11

## Question

Finish what "Build the semantic layer and its gates" starts, to the user's stated bar: **leave
no remainder.**

In scope:
1. Tag EVERY column in every family — sim_db (14 tables, ~173 columns), runtime, warehouse,
   inventory/profiles, affinity — until the completeness gate is empty across all of them.
   The census in `../assets/column-audit.md` section 2 is the seed; where it and the DDL
   disagree, the DDL comment scars win and become tags.
2. Logical renames at the named-query layer: `release_day` over physical `work_day`,
   `frame_index` over `shift_index`, `REPORTING_FRAME_SECONDS` over `SHIFT_SECONDS` (constant),
   plus any name the audit marked as lying. Physical names frozen.
3. Convert the audit's eight ranked at-risk reads to declared reads with use-assertions,
   `Diagnostics/receiving_report.py` (the 101x site) first; the `.con` escape-hatch reads
   still declare their uses.
4. Docs coherence: collapse the duplicated prose semantics (15-line DDL warnings, scar headers,
   memory restatements) into pointers at the declarations — one home per fact, per CLAUDE.md §6.

Done when: completeness gate empty over every family; the eight reads converted and asserting;
byte-identical everywhere (tags and read paths, no result changes); gates and narrowest tests
green; nothing committed without the user's go-ahead.
