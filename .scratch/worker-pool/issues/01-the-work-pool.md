# 01 - One cell-aware WorkPool for every run

Type: task
Status: resolved

Build the one pool: cell-tagged jobs, an own pending queue bounded to max_workers, absorb/drain, parent-thread continuations, the old _supervise recovery policy over all cells, one log listener, a required executor factory.

## Answer

Landed as develop 9bad3a97 with Tests/integration/test_work_pool.py (12 tests: the cross-cell barrier and its twin, bounded in-flight and weight order, continuations, rebuild only for cells with jobs left, quarantine, settlement, the no-wait exit).
