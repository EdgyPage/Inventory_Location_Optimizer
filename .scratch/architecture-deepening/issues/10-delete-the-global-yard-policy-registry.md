# 10 - delete the global yard policy registry

Type: debt
Status: needs-triage
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
