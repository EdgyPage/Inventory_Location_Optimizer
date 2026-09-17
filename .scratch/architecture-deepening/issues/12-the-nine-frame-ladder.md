# 12 - the nine-frame ladder, and caches that are interface without being declared

Type: refactor
Status: resolved

## Context

Adding one frame kind to the evaluation layer costs six edits across four files:

| Site | Where |
|---|---|
| `FRAME_TABLE` (kind -> sim-DB table), 9 kinds | `core/quantities.py:90-120` |
| `PAIRED_KINDS` -- a SECOND hand-kept subset of the same 9 | `:140` |
| 9 private cache dicts in `EvalContext.__init__` | `core/context.py:203-211` |
| 9 one-line pass-throughs (`def batch_df(self,k): return _requests.batch_frame(self,k)`) | `:248-273` |
| 9 near-identical 7-line bodies reaching into `ctx._bcache` **by private name, from another module** | `core/requests.py:132-269` |
| 14 `@request` registrations across 5 scopes | `:342-560` |

**`SiteContext` does not call `super().__init__`.** It re-implements it (`context.py:479-512`),
initialises **3 of the 9** caches (`:507-509`) and overrides 3 of the 9 accessors (`:534-549`). Its
own docstring records the consequence:

> Three of the four yard evaluations declare quantities and every one of them raised AttributeError
> on the first real coupled run, rendering nothing while the `[access]` summary reported a grant.

**Six of the nine kinds are still in that state.** A missing private attribute defeats the broker's
whole contract (`requests.py:12-16`: a MISSING resource never raises) by raising instead of
denying. Note the inconsistency: `_dcache` IS initialised and `drain_df` IS overridden, yet
`('site','drain')` is not a registered request -- so it works when called directly and is
ungrantable through `needs=`.

**Omitting `PAIRED_KINDS` misroutes silently.** `quantities.py:122-138`: `_metric_series` is
written as "batch, else the TASK frame", so a kind in `FRAME_TABLE` but not here is "a per-trailer
column looked up in a per-task frame, found absent, and returned as an empty Series" -- reported
downstream as unmeasurable rather than as misrouted.

**Coverage:** `yard_frame`, `drain_frame`, `missed_frame` and `shift_frame` appear in **zero** test
files; `carry_frame`, `free_index_frame`, `work_frame` and `site_batch_frame` in one each.
`test_request_broker.py` deliberately runs WITHOUT sim DBs, so the grant/deny logic is tested and
the nine bodies are not.

## What to build

Declare frames the way `quantities.py` already declares quantities:

```
FRAMES = {
 'batch': Frame(loader=load_batch_stats, builder=_bdf, table='batch_stats',
                paired=True,  deps=(),               scopes=('config','site')),
 'shift': Frame(loader=load_shift_days, builder=_sdf, table='shift_days',
                paired=False, deps=('batch','work'), scopes=('config',)),
}
```

`EvalContext` gets **one** cache (`self._frames: dict[tuple[str,str], Any]`) and **one** accessor,
`ctx.frame(kind, key)`. `requests.py` stops reaching into private attributes. `@request(kind,
scope)` registrations are generated from `Frame.scopes`, so `REQUEST_SCOPE`'s totality assertion
(`:114-118`) extends to frame-kind coverage. `FRAME_TABLE` and `PAIRED_KINDS` collapse into fields
on `Frame` and cannot disagree.

**Fog to resolve here:** whether `SiteContext`'s one honest override (`site_batch_frame`) can be a
per-scope builder in the table, letting `super().__init__` become callable again, or whether one
subclass survives.

## Verification

- One parameterised test: for every declared kind, on a fixture DB, load it, assert the builder's
  columns, assert the cache is hit the second time, and assert every scope in `Frame.scopes` can
  grant it. Takes the four zero-coverage kinds to covered in one file.
- A kind with no site builder must DENY, not raise. Assert that explicitly.
- Gates 1, 2, 10.

## Comments

`Optimization/Performance_Evaluations/` contains zero raw SQL because `quantities.py` got this
treatment. The frame ladder is the one rung that did not.

## Answer -- RESOLVED (2026-09-16)

### It was TWO merges, not one. The ticket's premise was half right.

`quantities.FRAME_TABLE` and `requests.py`'s nine loaders do **not** share a key space.
FRAME_TABLE keys on the kinds a QUANTITY declares -- `task_mean`, `task_sum`, `trailer`,
`carryover` -- and the loaders key on the frames a CONTEXT caches -- `task`, `yard`, `missed`,
`carry`, `free_index`. Eight against nine, overlapping but not equal. Forcing them into one
record would have invented a taxonomy neither side uses.

So:

**Merge 1 -- `FrameKind` in `quantities.py`.** `FRAME_TABLE` and `PAIRED_KINDS` were the pair
that COULD silently disagree, and they do share a taxonomy. One record per kind now carries
`table` and `paired`; both old names are derived from it and still exported (tests and
`headline/top_vs_baseline` read them). Verified the derived values are identical to the
hand-written originals, tuple order included.

**Merge 2 -- `_FrameSpec` + one cache in `requests.py`.** Nine near-identical bodies became one
table and one `frame(ctx, kind, key)`. The nine private cache dicts became one `self._frames`.
`extra` is a CALLABLE rather than a tuple because three kinds need real per-kind work: `task`
reads two maps off the context, `yard` needs the censoring bound and a threshold that must NOT
be consulted when there are no rows (the reason is the LOG, not cycles), and three kinds take
sibling frames.

### The distinction that had to survive, and did

`_arm_end_s` reads `ctx.batch_df(key)` so a SiteContext's override applies and the censoring
bound becomes the union of both leaves' clocks. The three sibling-frame hooks call the
module-level `frame(...)` instead, wanting this arm's own frame. That difference is load-bearing
and is now stated in the code rather than implied by which spelling someone happened to use.

`SiteContext` declares the same single cache as the base, so the six-missing-dicts
`AttributeError` class is gone by construction. Its `yard_df`/`drain_df` overrides were
byte-identical to the base and are deleted; `batch_df` is the one real override and stays.

### A bug this ticket's own test caught

The no-op override block is IDENTICAL in both classes, so a patch using `replace(..., 1)`
removed **`EvalContext`'s real accessors** instead of `SiteContext`'s duplicates. The full unit
tier passed anyway -- 2652 green with `EvalContext.yard_df` gone. That is the ticket's coverage
claim demonstrated rather than asserted: the yard frame at config scope had no unit test.
`Tests/unit/test_frame_specs.py::test_every_kind_has_an_eval_context_method` caught it.

### Verification

| check | result |
|---|---|
| `Tests/unit/test_frame_specs.py` | 9 passed (new) |
| `Tests/unit -k "not gpu"` | **2661 passed**, 1 skipped (2652 + the 9 new) |
| `analyze_run` on the toy run, end to end | exit 0, 12 artifacts written, **0** Tracebacks / ERRORs / denials / AttributeErrors |
| gates 1-5, 7-10 | green |
| gate 6 | RED, unchanged, pre-existing |

No digest needed: this is the analysis layer, and it reads simulation output rather than
producing it. The end-to-end `analyze_run` is the equivalent proof -- and checked for SWALLOWED
failures rather than trusting exit 0, because `analyze_run` logs and swallows by design.

### NOT done, deliberately

`Frame.scopes` and generating the `@request` registrations from the table. The registrations
encode kinds the loader table does not (`series`, `breakdown`, `runtime`, `whatif`,
`catalogue`) and omit three it does (`drain`, `carry`, `free_index` are read directly by the
equilibrium check, never brokered). Deriving one from the other would need a third taxonomy
reconciled first. The AttributeError class -- the actual harm -- is closed without it.
