# 12 - the nine-frame ladder, and caches that are interface without being declared

Type: refactor
Status: needs-triage

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
