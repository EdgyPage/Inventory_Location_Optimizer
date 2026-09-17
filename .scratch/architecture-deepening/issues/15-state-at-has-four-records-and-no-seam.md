# 15 - state_at has four reconstruction records and no seam, and Requires is unenforced

Type: refactor
Status: needs-triage
Blocked by: 11

## Context

**Part A -- the four records.** `Visualization/readers/base.py:534-549`:

```
def state_at(batch, aisles, t):
    state = self._state_from_spans(batch, aisles, t)          # 550-616
    if state is None: state = self._state_from_log(...)       # 617-693
    if state is None: state = self._state_from_keyframes(...) # 727-778
                              (+ _state_without_keyframes 779-828)
```

Four implementations of one contract (`RECONSTRUCTION.md` §1), selected by a hard-coded if-chain,
all private methods of one 1,150-line class, each writing its own f-string SQL against physical
tables (`bin_placement` `657-660`, `bin_eviction` `645-648`, `picks` `605/668/722/760`,
`bin_inventory` `799-802`), with scope filters built by string interpolation (`_int_list`, `:107`)
and a `_topup` CASE expression assembled from a vintage column check (`_has_col`, `:655-658`).

`Tests/architecture/test_viewer_broker_boundary.py:30-63` has to name each one to exempt it: **five
of its 14 allowlist entries are exactly these methods** plus `_apply_picks_upto_t`. An allowlist
keyed on private method names is a seam asking to exist.

Coverage: `test_viewer_protocol_conformance.py:18` is the only file importing `SqliteSimReader`
directly and it never opens a database -- reflection only. Real coverage arrives indirectly through
`Tests/integration/test_log_reconstruction.py`. Six public reader methods (`run_meta`,
`aisle_geometry`, `batch_index`, `bin_scores`, `sku_scores`, `aisle_state`) have no behavioural
test at all.

**Part B -- `Requires` is real only if a call site remembers to pass it.**
`Optimization/run_whatif_labor.py:68-73` declares a `compat.Requires` and **never passes it
anywhere** -- `_hours` at `:105` calls `connect.read_only` directly. Its sibling
`run_whatif_volume.py:96` threads the same declaration into `dataset.bind(..., requires=REQUIRES)`.
Two CLIs, same shape, one enforced.

And the read seam is half-adopted by package: `Performance_Evaluations/` has zero raw SQL;
`Visualization/` has ~14 raw reads under a shrink-only allowlist (managed debt); **`Diagnostics/` is
unmanaged** -- `receiving_report.py` has 17 raw SQL literals, a hand-rolled read-only URI connect
(`:341`) instead of `Schema.connect.read_only`, and a hand-rolled `sqlite_master` probe
(`:372-373`) instead of `Schema.capability`, while documenting the `dataset.override` pattern in
prose (`:753`) and binding nothing. `replay_run.py` adds 8 more plus a `PRAGMA table_info` probe
(`:250`). `Optimization/simdriver/scenario.py:41-42` has a bare `sqlite3.connect(db)` -- not
read-only, not immutable -- plus a `SELECT COUNT(*) FROM batch_stats`.

## What to build

Two independently valuable moves; take them in order and stop after A if B looks large.

**A.** Extract `SpanIndexSource`, `LogFoldSource`, `KeyframeSource`, `ArchiveSource` behind one
interface -- `available(reader) -> bool` and `state(batch, aisles, t) -> dict`. `state_at` becomes a
loop over an ordered tuple. The four already differ in exactly the two things an interface needs:
can I answer, and is the answer exact. The five name-keyed allowlist exemptions collapse to one per
adapter module.

**B.** Close the `Requires` enforcement hole. Either make `Schema.connect.read_only` take a
`requires=` and check it, or add an architecture test asserting every module-level `REQUIRES`
constant is referenced at a `bind`/`check_requirements` call in its own module -- which would have
caught `run_whatif_labor.py` immediately. Then bring `Diagnostics/` inside the same shrink-only
allowlist ratchet `Visualization/` already lives under.

## Verification

- Each adapter gets a direct behavioural test on a fixture DB; today only the composite is tested,
  and only indirectly. The currently-untested public reader methods can ride the same fixture.
- An unadopted `Requires` becomes a gate failure rather than documentation.
- Immutable-reader trap: a test that fakes a vintage in place must checkpoint, close, and assert
  the bound id first, or the loader silently reads the old page image (memory
  `immutable-readers-see-only-the-checkpointed-file`).
- WAL sidecars appearing beside an archive are created by `mode=ro` READERS and are not a
  writer-side bug (memory `wal-sidecars-come-from-readers`). Do not chase them.
- Gates 2, 3, 10.
