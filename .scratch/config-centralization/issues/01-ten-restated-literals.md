# The ten restated literals: the domain declares, settings imports

Type: task
Status: resolved

`settings.py:423` already states this effort's whole principle, and applies it once:

> The defaults are the kernel's declaration (`Warehouse/kernel/cost_model.py`), **imported
> rather than restated so the dataclass defaults and the run defaults cannot drift apart**.

Three crew-price scalars work that way. Ten other values do not: a domain-side dataclass default
or function default restates a number `settings.py` also declares. Each pair holds the SAME value
today, so unifying is provably value-preserving now; the point is that it stays one tomorrow.

Direction is forced by `context/architecture.yml`: `warehouse_core -> optimization`,
`wh_* -> optimization` and `inbound -> optimization` are forbidden. So the DOMAIN declares and
`settings.py` imports -- never the reverse.

## The triage, and why two sites are excluded

Not every co-occurring literal is a restatement. A value is in scope only when the two sites mean
the SAME thing, so that changing one without the other would be a bug rather than a choice.

IN SCOPE -- a real tuned value, restated:

| value | declared | restated at |
|---|---|---|
| target fill `0.85` | `settings.STORE_FILL`/`FF_FILL` | `inventory_planning.plan_warehouse(target_fill=0.85)` |
| batch shape `0.20`/`0.05` | `settings.FF_BATCH_MEAN`/`FF_BATCH_STD` | `BatchConfig` defaults, `Workload_Builder.py:60-61` |
| zoning bands `3` | `settings.ZONING_OFF['n_bands']` | `Inventory_Management.py:212`, `inventory_zoning.py:56` |
| dock doors `4` | `settings.INBOUND_DOCK_DOORS` | `Inbound/transit.py:98` AND `:353` |
| fee / urgency days `2.0`/`0.0` | `settings.INBOUND_FEE_THRESHOLD_DAYS` / `..._URGENCY_HORIZON_DAYS` | `Inbound/gain.py:456-457` |
| `FF_BATCH_SEED_OFFSET` | `channels.py:141` | re-typed as `1_000_000` at `channels.py:171` |

OUT OF SCOPE, deliberately:

* **`PutQueueSpec.swap_coef = 0.0`** (`put_queue.py:104`, `:442`). It reads as a duplicate of
  `settings.PUT_SWAP_COEF = 0.0`, and it is not. On the domain side `0.0` is the "no cart model"
  SENTINEL -- the docstring says the cart is "None = unbounded, i.e. no cart model, which is every
  queue that has not asked for one". If an operator set `PUT_SWAP_COEF = 5.0`, a `PutQueueSpec`
  built with no cart must still charge nothing. Coupling them would make the sentinel follow the
  knob, which is a behaviour change dressed as a cleanup.
* **`BatchConfig.sampler = 'v1'`** against `settings.SAMPLER = 'v3'`. These genuinely differ, and
  `channels.py:81-83` records why: *"default here matches BatchConfig's own 'v1' so non-runner
  constructions (tests, Diagnostics) keep their frozen historical meaning; the runner passes the
  era in"*. That is a decision, not drift. The real defect it causes is in the MEASUREMENT
  fixture, not here -- see `complexity-round` ticket 01.

## Acceptance

* Each in-scope pair resolves to ONE object, proven by a test that fails at import on divergence.
* `pytest Tests/unit -q` green; `verify_architecture.py` exits 0 (each new import edge checked).
* No number moves anywhere: this is a no-op by construction.


## Answer

**Five pairs unified, three excluded on inspection, and the list was narrower than the plan said.**

### What landed

The domain declares; `settings.py` imports. Five new declarations:

| declaration | in | read by |
|---|---|---|
| `DEFAULT_TARGET_FILL = 0.85` | `Warehouse/inventory/inventory_planning.py` | `plan_warehouse`'s default, `settings.STORE_FILL`, `settings.FF_FILL` |
| `DEFAULT_ZONING_BANDS = 3` | `Warehouse/inventory/inventory_zoning.py` | `configure_zoning`'s default, `Inventory_Manager.__init__`, `settings.ZONING_OFF`, and the WORKER's fallback |
| `DEFAULT_DOCK_DOORS = 4` | `Inbound/transit.py` | both transit classes, `settings.INBOUND_DOCK_DOORS` |
| `DEFAULT_FEE_THRESHOLD_DAYS = 2.0` | `Inbound/gain.py` | the gate entry, `settings.INBOUND_FEE_THRESHOLD_DAYS` |
| `DEFAULT_URGENCY_HORIZON_DAYS = 0.0` | `Inbound/gain.py` | the gate entry, `settings.INBOUND_URGENCY_HORIZON_DAYS` |

Plus `channels.py:171`, which had re-typed `1_000_000` thirty lines under
`FF_BATCH_SEED_OFFSET = 1_000_000`, now reads the name.

**`settings.py` importing `Inbound` is legal** -- the fog item is closed. Only
`inbound -> optimization` is forbidden; nothing forbids the reverse, and
`verify_architecture.py` reports `architecture OK - 3489 nodes, 4245 edges, 20 backbone,
25 layers verified` with the new edges in the graph.

### The one that mattered most was not on the list

`n_bands` had **four** literals, not three. The fourth is
`strategy_runner.py:1454`'s `int(_zcfg.get('n_bands', 3))` -- a fallback **inside the worker**.
That is the dangerous shape: a spawned worker's fallback is invisible to all five seams, so had
`ZONING_OFF` changed, the old value would have survived in the worker process, where nothing
reads back and no `run_spec.json` records it. Compare `sim_config.py:1014`, which does the same
job correctly by falling back to the NAMED kernel default.

### Three exclusions, and why the plan's count was wrong

The plan said "ten restated literals". On inspection three are not restatements at all:

* **`PutQueueSpec.swap_coef = 0.0`** is the "no cart model" SENTINEL, not a copy of
  `PUT_SWAP_COEF`. Binding them would make a cartless queue start charging when an operator
  tunes the knob -- a behaviour change dressed as a cleanup.
* **`BatchConfig.sampler = 'v1'`** vs `settings.SAMPLER = 'v3'` -- a recorded decision
  (`channels.py:81-83`), kept so non-runner constructions retain frozen historical meaning.
* **`BatchConfig.mean_fraction = 0.20`** equals `FF_BATCH_MEAN` by coincidence and differs from
  `STORE_BATCH_MEAN` (0.15). Binding it to either asserts a relationship that does not exist.

The distinction that decides each: two sites are one value only when changing one without the
other would be a BUG. Where it would be a CHOICE, they are two values that happen to agree.

### The guard

`Tests/unit/test_declared_once.py` -- 11 tests, asserting **identity, not equality**, because
two literals both reading 0.85 satisfy `==` and are precisely the state being forbidden. It also
pins the three exclusions, so a later sweep cannot "finish the job" and change behaviour while
believing it is tidying.

**Proven non-vacuous**, not merely passing: sabotaged both ways in a throwaway process -- a
second equal float literal for `STORE_FILL`, and the worker fallback rewritten back to a bare
`3` -- and both were caught. (Note recorded in the test: CPython interns small ints, so `3 is 3`
holds for independent literals; `DEFAULT_ZONING_BANDS` is therefore fenced by source inspection
rather than by identity, and the test says so.)

### Gates

`pytest Tests/unit -q` 2584 passed / 1 skipped; `Tests/integration` 425 passed / 1 skipped;
`verify_architecture.py`, `verify_context.py`, `path_guard`, `docref_guard`,
`runschema.contract --check`, `Schema.profile_tree --check`, `verify_memory.py` all exit 0.

`settings.py` is a `SHAPE_SOURCES` file, so its fingerprint moved; full `preflight` proved the
shape with both canaries and reported **tree shape UNCHANGED -- schema 341e1422457c still
valid**. No number moved anywhere, which is the acceptance criterion this ticket was written to.
