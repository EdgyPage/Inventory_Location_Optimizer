# 03 - Every cell of a run through the one pool; delete the per-cell executor

Type: task
Status: resolved

scenario._run_cells streams cells into the pool; delete _run_scenario, _run_workers_flat, _supervise, _run_pool; retarget every test that faked them; drive the real spawn pool through WorkPool.finish.

## Answer

Landed as develop 70633401. Assets live until the cell settles (a retry never rebuilds them), the freeze is scoped, the blank-arm scan is per cell, a setup raise is reported not unwound, a failed prepare is reported. 193 driver tests + the real-spawn broken-pool test + the coupled resume e2e green.
