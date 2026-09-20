# 08 - _cell_complete answers complete on a torn coupled pair

Type: task
Status: open

cells._cell_complete returns True on ONE sim_meta.json per pair, so a resumed multi-cell run skips a cell whose coupled pair is torn (one leaf finalized, one not) before _reconcile_coupled_unit could repair it. Found while retargeting Tests/e2e/test_coupled_resume_e2e.py, which drives scenario._run_cells directly and never meets the skip. Fix: a cell is complete when every LEAF the layout declares is complete (workunits._leaf_is_complete over the pair's channel-run dirs), and a test that plants a torn pair under a two-cell tree and resumes through _run_whatif_matrix.
