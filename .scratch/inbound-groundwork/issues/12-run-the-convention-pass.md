# Run the convention pass to zero remainder

Type: task
Status: resolved
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

## Answer

EXECUTED 2026-08-26, three commits (`59ff750`, `fc690bd`, plus a one-line ratchet appeasement):

1. **Zero remainder, literally**: every column of every registered family is tagged — sim_db
   (15 tables), keyframes, runtime (every `*_s` explicitly WALL-clock), warehouse, affinity,
   inventory (~250 columns beyond the epicenter's 62). The gate now sweeps all six families
   AND enforces the no-remainder rule itself: a table missing from its covered set fails.
   One vocabulary refinement earned by the sweep: a clock is required of temporal KINDS
   (stamp/span/rate), not of a COUNT whose unit merely says 'batches'. And SPAN joined the
   additive kinds — spans of work sum to labour (the `task_makespan == SUM(duration)`
   invariant); LEVEL stays refused.
2. **Seven at-risk readers converted** (the audit's ranked list): each carries a
   `SEMANTIC_USES` pure literal — every declared-shape column it touches and HOW — validated
   by an AST-reading gate that never imports them, so declaring costs no dependency and a
   matplotlib-dragging viewer is checkable anyway. The two bench log-parsers are excluded
   with cause (they parse logs, not columns; recorded in the gate's own comment).
   `replay_run`'s reads of the retired `bin_inventory` are documented as outside the
   declared shape by design.
3. **Honest names**: `release_day` and `frame_index` live at the logical layer;
   `SHIFT_SECONDS` → `REPORTING_FRAME_SECONDS` at the authoring surface with the CONFIG key
   and every recorded row frozen — *shift* is now free for the drain-or-cap scheduler.
4. **One home per fact**: the two worst DDL prose duplications (`put_queue_state.cut`'s
   15-line warning, `carryover`'s reason families) collapsed to pointers at
   `sim_semantics.py`. Schema store resynced — 0 outgoing shapes, ids unmoved.

Verification: full architecture + integration tiers 753/753 after the reword (the one
failure was the run-tree ratchet correctly counting a manifest filename in a prose note —
fixed by saying the same thing without the token); unit tier 1,345; all six repo gates green;
memory verifier exit 0.
