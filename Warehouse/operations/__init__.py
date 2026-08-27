"""operations — WHO does the work: roles, modes, workers, crews.

The domain's actor model plus the operations themselves, split out from the pick simulation
so a second work stream can reuse it.  Imports the kernel and the VALUE layers (`wh_layout`,
`wh_catalog`) but none of the machinery that consumes them -- not the pick simulation, the
placement engine, the assignment functions or the run harness -- which is what let the
top-level `Inbound/` package build its crew of unloaders on these same layers with no
dependency inversion.  (It landed beside `Warehouse/`, not inside it as this line once
predicted, and took `unload.py` and `inbound.py` -- now `pack.py` -- with it.)

    Role     pick | put
    Mode     foot | machine          -- the axis that decides speed
    Worker   uid + local_id + role + mode + SpeedProfile
    Crew     a pool of identical workers, and the uid allocator across pools

The speed VALUE object (`SpeedProfile`) deliberately lives in `Warehouse/kernel/cost_model`,
not here: `cost_model` and the placement scorers consume speeds, and `architecture.yml`
forbids `wh_kernel -> *`, so the kernel cannot import this package.  The value lives in the
kernel; whose value it is lives here.
"""
from Warehouse.operations.roles import Mode, Role
from Warehouse.operations.worker import Crew, Worker

__all__ = ['Mode', 'Role', 'Crew', 'Worker']
