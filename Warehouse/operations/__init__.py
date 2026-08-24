"""operations — WHO does the work: roles, modes, workers, crews.

The domain's actor model, split out from the pick simulation so a second work stream can
reuse it.  Imports `Warehouse/kernel/` and nothing else, so a future `Warehouse/inbound/`
needs no dependency inversion to build a crew of unloaders.

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
