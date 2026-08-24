# `Warehouse/operations/` — who does the work

The domain's actor model: roles, modes, workers, crews. Split out from the pick simulation
so that a second work stream — put-away, and later an inbound unloading crew — reuses it
instead of reimplementing it.

## What belongs here

- **Actor identity and classification.** `Role` (pick / put), `Mode` (foot / machine), and
  anything else that answers *who is doing this*.
- **Pools of actors.** `Crew`, and the allocation of unique ids across pools.
- Later: a put crew's own profile, once put-away consumes simulated time.

## What does NOT belong here

- **The speed value object.** `SpeedProfile` lives in `Warehouse/kernel/cost_model.py`.
  `cost_model` and the placement scorers consume speeds, and `context/architecture.yml`
  forbids `wh_kernel → *` — so the kernel cannot import this package, and putting the value
  here would force an inversion. The value lives in the kernel; *whose* value it is lives
  here.
- **Anything that reads the run harness.** This package imports `Warehouse/kernel/` and
  nothing else. `wh_operations → optimization` is forbidden in `architecture.yml` for the
  same reason it is forbidden for `wh_picking`: a crew is built from picklable values handed
  to a worker, never from `CONFIG`.
- **Scheduling.** Which worker gets which task is `Warehouse/kernel/allocation.partition`
  plus `Pick.assign_tasks`. This package says who exists, not who does what.
- **Event records.** A `PickEvent` stays in `Warehouse/picking/`; the merged cross-stream
  event row is a persistence concern.

## The two id spaces

A `Worker` carries two ids and they are not interchangeable:

| field | space | who reads it |
|---|---|---|
| `local_id` | dense within one crew, `0..size-1` | `picker_events`, `_group_events_by_picker`, `progress_at`, the viewer |
| `uid` | unique across every crew in the run | the merged event stream |

Every reader that predates a second crew assumes the dense per-crew form —
`_group_events_by_picker` allocates exactly `k_pickers` lists and **raises** on anything
outside `[0, k)`, and `progress_at` enumerates `range(num_pickers)`. A `uid` that leaked
into a `local_id` slot would land inside that range and be silently accepted, which is
exactly why they are two named fields rather than one encoding.
