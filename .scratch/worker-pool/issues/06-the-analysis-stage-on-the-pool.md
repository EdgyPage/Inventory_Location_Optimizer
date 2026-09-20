# 06 - analyze_cells: every cell's three stages through the one pool

Type: task
Status: resolved

One pool over every cell; a continuation builds a cell's aggregate and site jobs once its config jobs resolved; run_analysis(cell_dir) is the one-cell case; the cell record from run_layout.json is applied while a cell's jobs are built; per-cell tallies; a rebuild re-emits job dicts, never the builders.

## Answer

Landed as develop 53964420 with Tests/unit/test_analysis_stage_pool.py (8 tests). Fixes two pre-existing wrongs in the analysis of inbound and split sweeps (see the map).
