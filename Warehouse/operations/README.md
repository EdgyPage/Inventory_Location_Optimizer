# `Warehouse/operations/` — who does the work

The domain's actor model: roles, modes, workers, crews. Split out from the pick simulation
so that a second work stream — put-away, and later an inbound unloading crew — reuses it
instead of reimplementing it.

## What belongs here

- **Actor identity and classification.** `Role` (pick / put), `Mode` (foot / machine), and
  anything else that answers *who is doing this*.
- **Pools of actors.** `Crew`, and the allocation of unique ids across pools.
- **The work each role does, as a named operation.** `putaway.put_cost` (what a put costs);
  `inbound.receive` (what an arrival packs into).
- Later: a put crew's own profile, once put-away consumes simulated time.

## What does NOT belong here

- **The speed value object.** `SpeedProfile` lives in `Warehouse/kernel/cost_model.py`.
  `cost_model` and the placement scorers consume speeds, and `context/architecture.yml`
  forbids `wh_kernel → *` — so the kernel cannot import this package, and putting the value
  here would force an inversion. The value lives in the kernel; *whose* value it is lives
  here.
- **Anything that reads the run harness.** `wh_operations → optimization` is forbidden in
  `architecture.yml` for the same reason it is forbidden for `wh_picking`: a crew is built
  from picklable values handed to a worker, never from `CONFIG`. Three more edges are
  forbidden alongside it — `wh_picking`, `wh_inventory`, `wh_placement` — so an operation
  never depends on the machinery that consumes it.

  What IS allowed, and what an older version of this sentence wrongly denied: the **value**
  layers. `wh_kernel` for the cost primitives, and `wh_layout` / `wh_catalog` because an
  operation on merchandise has to know what a pallet and an `Order` are — `inbound.receive`
  calls `viable_storage_units`. The rule is *values in, machinery out*, and the four `forbid`
  entries are its whole enforcement.
- **Scheduling.** Which worker gets which task is `Warehouse/kernel/allocation.partition`
  plus `Pick.assign_tasks`. This package says who exists, not who does what.
- **Event records.** A `PickEvent` stays in `Warehouse/picking/`; the merged cross-stream
  event row is a persistence concern.
- **Trailers, docks, and how a shipment gets split.** `inbound.receive_all` takes the split
  as a list of quantities and says nothing about where the list came from; the manager's
  `inbound_split` hook is where a caller supplies one. A load-planning model is a *producer*
  into that seam, not a member of this package — and it will need the run harness, which
  `wh_operations → optimization` forbids here.

## Why packing lives with the arrival

`viable_storage_units(order, quantity)` has always taken a quantity, so what a shipment packs
into has always depended on how much of it shows up at once. Nothing named that, so nothing
could express *an order that would be palletized arriving whole, and lands as two singleton
packs when a trailer splits it*. `inbound.LoadPlan` is that name; it wraps the packer
unchanged and carries the counterfactual (`unsplit()`, `split_penalty()`) so the cost of a
split is a number rather than an assumption. Measured, splitting changes the packing for
roughly 40% of planned orders — and in **both** directions, so "splitting is worse" is not
safe either.

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
