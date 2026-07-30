# metrics — turning simulation events into numbers

Sits between the simulation and both of its consumers: the worker (which records stats into a run
DB) and the analysis suite (which plots them).

| Module | Owns |
|---|---|
| `Simulation_Analytics.py` | per-batch and per-task statistics from the picker event stream |
| `Workload.py` | the analytical per-aisle workload (W) model, mirroring the sim's pick-cost primitives |

**Why it is not under `Performance_Evaluations/`:** `strategy_runner` imports
`Simulation_Analytics`. Filing it under the analysis package would create an
`optimization → evaluations` dependency, inverting the direction the architecture boundaries
intend.

**Does NOT belong here:** storing the numbers (→ `persistence/`) or drawing them
(→ `Performance_Evaluations/`).
