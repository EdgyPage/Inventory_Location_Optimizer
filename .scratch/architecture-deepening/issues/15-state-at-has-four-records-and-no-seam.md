# 15 - state_at has four reconstruction records and no seam, and Requires is unenforced

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17  (3e2525d8 part B, f850d01e part A)

### Part A -- the four records

`Visualization/readers/state_sources.py`. `base.py` 1,150 -> 900 lines; `state_at` is a loop
over `SOURCES` in cost order.

**The fourth record was hidden inside the third.** `_state_from_keyframes` called
`_state_without_keyframes` from its own body, so "this run has a fourth record" was a fact you
learned by reading a method. It is an entry in the ordered tuple now.

**CORRECTION: `available()` is not in the interface.** The ticket asks for
`available(reader) -> bool` beside `state(...)`. Two questions where one suffices, and two that
cannot be kept in agreement: `SpanIndexSource` HAS a sidecar index and still returns None when
that index holds no row for the batch -- which the original method does today and must keep
doing. `available()` saying True there would be wrong; opening the index to find out would run
the query twice. A source answers exactly once, and the ORDER is the policy.

The terminal contract became explicit as a result: `ArchiveSource` never returns None, and
`state_at` asserts that rather than trusting it.

**CORRECTION: one module, not four.** The ticket says "one per adapter module". The four share a
base, the `_apply_picks_upto_t` helper, and an ordering that only means anything read together;
four modules need a fifth for the tuple. One module holding an interface, its adapters and the
table naming them is what `checkpoint_buffer.py` and `leaf_scope.py` already do here.

**The allowlist keys on QUALNAME now.** All four adapters implement `state`, so a name-keyed
exemption folds them into one entry and a fifth source's raw SQL becomes invisible. The scanner
emits `Class.method`, which also makes an entry say which RECORD it is about.

### Part B -- the enforcement hole

`run_whatif_labor.py` declared a `Requires` and never passed it; `_hours` called
`connect.read_only` directly while its sibling threaded the identical shape. Fixed, and gated.

**CORRECTION: the proposed rule is false for several modules.** "Every module-level REQUIRES is
referenced at a bind/check_requirements call in its own module" would fire on
`Performance_Evaluations/core/era.py` (which SPLITS its declaration deliberately and re-checks
per quantity), `Visualization/readers/base.py` ("declared -- the compatibility gate CI
validates", its own words) and `db_reader.REQUIRES_DISCOVERY` (shape-following, because discovery
probes ANY vintage including unvetted ones it then skips). The gate is "threaded, OR listed as
declaration-only WITH the reason", shrink-only, with non-vacuity in both directions.

**And it found three more on its first run.** There are TEN declarations in the tree, not the
seven a `^REQUIRES = ` grep sees: `QUANTITY_READS`, `GATED_READS` and `REQUIRES_DISCOVERY` are
not spelled `REQUIRES`. All three were declaration-only by design; none was a hole. None was
visible either.

**Diagnostics joined the ratchet** (`Tests/architecture/test_diagnostics_read_boundary.py`): 24
raw SELECT literals across 10 functions, a hand-rolled read-only URI and two hand-rolled
capability probes. The ten entries share ONE honest reason -- "unmigrated when the ratchet was
installed" -- rather than ten invented ones; the viewer's entries have specific justifications
because each was argued as that package migrated, and these were not. The two structural defects
get named assertions instead, including the one that is NOT trivially fixable: memory
`wal-sidecars-come-from-readers` -- a `mode=ro` open creates `-wal`/`-shm` beside an archived DB
and cannot remove them, so which opener is used decides what an archive looks like afterwards.

### Verification

| check | result |
|---|---|
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,260 passed / 2 skipped |
| `Tests/integration/test_log_reconstruction.py` | 27 passed (was 15; +12 for the adapters and the six untested public methods) |
| `Tests/architecture/test_viewer_broker_boundary.py` | 5 passed |
| `Tests/architecture/test_diagnostics_read_boundary.py` | 5 passed |
| all ten gates | green |

No byte-identity run: the viewer reads finished runs and writes no simulation output.

### What this unblocks

Nothing -- 15 was the last of the read-seam tickets.
