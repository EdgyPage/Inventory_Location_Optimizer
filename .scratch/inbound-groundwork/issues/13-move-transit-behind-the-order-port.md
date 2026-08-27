# Move transit behind the order port

Type: task
Status: open

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
