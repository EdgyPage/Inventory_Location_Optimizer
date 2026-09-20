# 05 - The worker imported sim_config at run time; the asset builder snapshotted CONFIG at import

Type: task
Status: resolved

Move load_run_inventory to a CONFIG-free module; extend the guard to function-body imports; make keyframe_interval a call-time default.

## Answer

Landed as develop 99982d90. The import-time probe stayed green for months while every spawned worker imported sim_config through a function-body import; the guard now walks those one level, with a sabotage twin.
