# 10 - delete the global yard policy registry

Type: debt
Status: resolved
Blocked by: 09

## Context

`Inbound/priorities.py:128` declares `GLOBAL_POLICIES` with **one entry**, `fifo`. It is not merely
single-adapter -- it is **unreachable on the path anyone runs**:

- `Inbound/transit.py:364` -- `YardTransit.__init__` calls
  `super().__init__(..., global_policy='fifo', ...)`, hardcoded.
- `Inbound/priorities.py:11` -- "the GLOBAL registry and its knob stay untouched and are simply
  unread in standing mode."

The standing yard is what the whole phase-2 campaign runs under. So `INBOUND_GLOBAL_POLICY` pays
the full config tax -- settings constant (`settings.py:179-182`), CONFIG key
(`sim_config.py:189-191`), `INBOUND_KEYS` membership (`:725`), CLI flag
(`run_simulation.py:849-860`), run-spec record, two restore sites, `inbound_spec` handling
(`:877-879`), a `TrailerTransit._global` slot (`transit.py:98-113`), and the registry itself -- and
can only ever take one value.

`INBOUND_TRAILER_BOUND` self-documents at `settings.py:182` as "inert under fifo -- shipped for the
interface, by decision". `bounded_order`'s key-entry branch (`priorities.py:223-229`) is an
O(n x bound) repeated-max loop no production configuration reaches.

## The decision, already taken

**Delete `GLOBAL_POLICIES`; keep `LOCAL_POLICIES`.** The standing yard REPLACED the global ranking
rather than deferring it, so it cannot come back. Deletion test on `global_key`
(`priorities.py:174-183`): complexity **vanishes** -- one caller, one entry. That is the definition
of a pass-through. `LOCAL_POLICIES` (the order items come off one trailer) is a plausible future
axis and cheap to keep.

Contrast with the seams that are real and stay: `YARD_POLICIES`/`DOCK_POLICIES` (6 adapters --
`fifo`, `lifo`, 4 gain), crew allocation (2: split, merged), trailer type (2: `Trailer53`,
`Trailer28`).

## What to build

Remove `GLOBAL_POLICIES`, `global_key`, `TrailerTransit._global`, `INBOUND_GLOBAL_POLICY` and its
config sites. Replace the v1 ranking with the `-seq` sort it already is. `INBOUND_KEYS` loses an
entry; `inbound_spec` loses its lines.

**Do NOT delete `bounded_order`.** Its ORDERING branch (`priorities.py:207-220`, the permutation
check) is load-bearing and exercised by `test_bound_composes_bound_first_with_a_gain_entry`. Only
the key-entry bound arithmetic is unreached -- decide separately whether that goes with
`INBOUND_TRAILER_BOUND` or stays.

## Verification

- `test_campaign_cells_can_run.py` and `test_gain_plan.py:169` assert over
  `YARD_POLICIES`/`DOCK_POLICIES`; confirm neither reaches the deleted registry.
- A run-spec written before this change must still restore. Check `_apply_run_spec` and
  `_apply_run_shape` tolerate the absent key rather than raising -- an archived run's spec carries
  it.
- Gates 1, 2, 8, 10.

## Answer -- RESOLVED (2026-09-16)

`GLOBAL_POLICIES`, `global_key`, `INBOUND_GLOBAL_POLICY`, the `--inbound-global-policy` flag,
the CONFIG key, the `Knob`, the `inbound_spec` entry, the driver kwarg and
`TrailerTransit`'s now-unused `global_policy` parameter are all gone. v1's transit calls
`_fifo_trailer` directly.

### One correction to this ticket's reasoning

It said the registry was "unreachable on the path anyone runs". That is true of the STANDING
YARD, but not of the repo: `TrailerTransit` is still constructed in production, on the
inbound-on / standing-yard-off branch (`strategy_runner.py:1782`), and it did read the
registry. So the registry was reachable -- it simply could never hold a second entry, because
`YardTransit` REPLACED the decision rather than deferring it and any value but `'fifo'` raised
`KeyError`. One adapter, and no route by which a second could arrive.

The deletion is the same either way; the argument for it is "a seam with one adapter that
cannot gain another", not "dead code".

### Kept, with reasons

- **`LOCAL_POLICIES`** -- also single-entry, but the order items come off ONE trailer is a
  plausible future axis and was never replaced by anything.
- **`bounded_order`** -- its ORDERING branch (the permutation check) is load-bearing and
  exercised by `test_bound_composes_bound_first_with_a_gain_entry`.
- **`INBOUND_TRAILER_BOUND`** and the key-entry bound arithmetic -- `settings.py` records it as
  "inert under fifo -- shipped for the interface, by decision". That is a standing decision of
  the repo's, not an oversight, and this ticket is not the place to overturn it.

### Verification

`Tests/unit -k "not gpu"`: **2671 passed**, 1 skipped -- no test referenced the deleted
registry. e2e + integration run separately because the toy run's profile is inbound-OFF
(`--spec scheduler_ab`), so a digest would not have exercised `TrailerTransit` at all; that
gap is worth knowing before anyone trusts a tiny-profile digest for an inbound change.

An archived `run_spec.json` still carrying `inbound_global_policy` is harmless: the resume
restore iterates `SPEC_KNOB_NAMES` and `_apply_run_shape` iterates `INBOUND_KEYS`, and the key
is in neither, so it is ignored rather than re-applied.
