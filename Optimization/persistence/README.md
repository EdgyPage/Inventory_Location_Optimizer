# persistence — SQLite schemas and read/write for a run's DBs

Owns the DDL and the load/save helpers for every database a run produces. If you are adding a
**table** or a **query**, it goes here.

| Module | Owns |
|---|---|
| `Picking_Data.py` | `sim_<arm>.db` — runs, batch/task stats, picker events, picks, the bin-mutation log (`bin_placement`/`bin_eviction`), the yard's raw stamps and per-drain levels (`yard_trailers`/`yard_drains`), keyframes, scores. Also the READER for the retired `bin_inventory`, kept so archived runs stay openable |
| `Warehouse_Data.py` | `warehouse.db` — sizing stats and aisle layout |
| `runtime_metrics.py` | `runtime_metrics.db` — per-arm compute cost; the only DB carrying a `cell` column |

**Does NOT belong here:** path construction (→ `runschema/`, which owns where files live),
orchestration (→ `simdriver/`), or metric computation (→ `metrics/`, which turns events into
numbers; this package only stores them).

## The invariant worth keeping

Every module here imports **nothing else from `Optimization`**. That is deliberate: persistence is a
leaf, so a spawned worker can save results without dragging configuration or the driver into its
process. Adding an intra-package import would quietly undo that — `Phase 7`'s boundary rules exist
to catch it.
