# Move transit behind the order port

Type: task
Status: resolved

## Question

Execute the transit half of the broker decision ("Draw the Inbound package boundary" Q4=b,
"Choose the lead-time denomination"): the lead queue's TIMING machinery moves behind the
manager's order port as an injected Inbound transit object; the manager keeps only its scalar
ledger (`_deferred_qty` credit/debit — the zero-edit property). Byte-identical throughout: the
default transit object reproduces `[sku, qty, remaining_lead]` batch ticking exactly.

In scope: the injected transit object in `Inbound/` (flag-off default = batch countdown,
identical semantics); the seven phase WRAPPERS stay on the manager and delegate
(`test_reorder_phases` pins call sites, not bodies); an accessor replacing the
`strategy_runner.py:936` private `_lead_queue` read (the replay snapshot's 3-tuple shape
preserved); `in_transit_qty`/`lead_queue_depth` reading through the ledger or accessor
unchanged; `test_lead_time_unit`'s note rewritten per its own instructions (the pick is made:
legacy batches flag-off, trailer time-leads flag-on — the note must keep the word "trailer").

OUT: trailers themselves (next ticket), the seconds-denominated lead path (arrives with them).

Done when: all gates green, `test_reorder_phases` + `test_lead_time_unit` +
`test_inbound_load_plan` + the unit tier pass untouched-or-updated-with-cause, replay rows
byte-identical, nothing committed without the user's go-ahead.

## Answer

EXECUTED 2026-08-26. `BatchTransit` in `inventory_reorder.py` (module scope, beside the
default packer — the manager-local default the zero-import rule requires; the INJECTED
trailer transit lands in `Inbound/` with ticket 14, on the same `mgr.transit` seam):
`dispatch` / `advance` (the one-batch tick, relocated verbatim) / `release` (in-order,
negative remainders never clamped) / `depth` / `merchandise()` / `snapshot()`. The manager
keeps only the scalar `_deferred_qty` ledger; all seven phase wrappers stay and delegate
(the ratchet pins call sites, and `test_lead_time_unit` now ALSO pins the delegation);
`_lead_queue` survives as a property window so every historical reader and `__new__`-built
test manager works unchanged; `strategy_runner` reads the new public `transit_snapshot()`
(3-tuple shape preserved) instead of the private attribute. The `LEAD_TIME_UNIT` note now
records the pick its own test demanded be made "with the trailer model in hand": flag-off
batches via `BatchTransit`, flag-on absolute-clock trailer leads. One position-pinned test
taught a lesson: the note-location guard reads the FIRST occurrence of its token, so the
transit docstrings reference "the unit note" without naming it. Byte-identical by
construction; 1,740 unit+integration + 6 receiving-e2e green; all gates exit 0. Committed.
