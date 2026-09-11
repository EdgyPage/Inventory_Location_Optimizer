# Design the site put-away pool

Type: grilling
Status: resolved

HITL. Skills: `grilling` + `codebase-design`.

## Question

The charter makes put-away **one site pool over segregated volume**: a putter takes work from either
channel's queue, a cart carries one channel's packs only, and the two leaves share a per-day pool of
crew-seconds rather than a clock. Design the mechanism.

1. **The uid allocator.** `strategy_runner.py:946-1006` (`_put_crew`, `_put_crews`,
   `_bind_put_crews`, `enable_putaway_timing`) builds put crews per arm from `put_crew_spec()` and
   slices them by `_put_crew.workers(uid)` off the pick crew's uid sequence. Two leaves in one
   process each slicing the same site crew **double-book the same workers**. Decide the single
   allocator across both leaves — and check the fifth seam: `workunits._shared` (memory
   `config-knob-has-five-seams`), or the knob silently reverts to its default in every spawned
   worker.

2. **The day budget.** Each leaf's put-away runs as today (`inventory_reorder.py:946-954`,
   `Inventory_Management.py:1383` `_stock`, `:1493` `_stock_per_unit`, `:1821` `_admit`) but the two
   `_stock` calls draw one pool of crew-seconds and one `put_deadline`. Decide the allocation rule:
   what stops the leaf that runs first from consuming the whole day's budget, and what a leaf sees
   when the pool is exhausted (the same cut it sees today, or a new state?). The memory
   `cut-is-a-level-not-a-flow` is the trap: whatever this reports, a level cannot be summed over
   batches.

3. **The source stamp.** `_admit` stamps `source` from `getattr(self.transit, 'SOURCE', ...)`
   (`inventory_reorder.py:481`) — under a shared transit both leaves read the same object. Confirm
   what `source` means on a coupled run and whether the carryover table's key still separates its
   two producers (memory `carryover-two-producers-one-key`).

4. **The put price.** `put_deadline` is a START gate, not a duration (memory
   `working-day-clock-plan-corrections`). Confirm the shared budget composes with that, and with the
   per-channel put price (department-calibration 26) — the site pool is one crew, but the two
   channels' per-unit put costs differ.

5. **What flag-off keeps.** Byte-identical: an inbound-off or single-channel run keeps today's
   per-leaf crew, double count and all. State the switch explicitly so the equivalence test has
   something to assert (CLAUDE.md §2: a new feature must be a strict no-op when its flag is off).

6. **What the band reads.** `equilibrium.py:390-397` already compares a leaf's put load to a site
   crew, and `expected_utilization`'s "single-channel leaves undercut ρ" caveat
   (`staffing.py:663-667`) exists precisely because of the double count. Decide what the put clause
   reads on a coupled run — ticket 07 owns the report, this ticket owns the number it reads.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§1, §4.

## Answer

**The site put pool IS a shared `list[float]`.** Both leaves' put queues are bound to the same
clock list, so `crew_clock.charge` greedy-books every put to whichever putter is free earliest
across both channels — a putter genuinely taking work from either queue, with no new scheduling
vocabulary. The day is divided by the two channels' own recorded expectations, with a residue pass
that makes "a channel that finishes early releases labour to the other" literally true. One new
module owns the rule, the shared list and its reset.

### 1. The mechanism: the clock list itself

`crew_clock` is deliberately *functions over a bare `list[float]`* (`crew_clock.py:11-13` — "a base
class is exactly the hierarchy it refuses"), and `reset()` mutates **in place** so "a caller
holding the list keeps the one the owner uses". That design is what makes the pool nearly free:
`PutQueue.bind_crew` (`put_queue.py:224`) gains an optional pre-built `clocks`, and both managers'
queues are handed **the same list**.

Segregation survives untouched: the two managers keep their own queues, carts, geometries and
`_put_seconds`. Only the people are shared, which is exactly the charter's "one site pool over
segregated volume".

**Rejected: a shared seconds ledger.** Keeping per-leaf clock lists and sharing only a scalar
"crew-seconds remaining" preserves today's arithmetic, but it invents a second labour model beside
`crew_clock`, and `work_events.put_rows` would then attribute rows to workers whose clocks are
fiction.

**The reset is the one behavioural change the shared list demands.**
`drain_putaway_records()` resets every queue's clocks (`Inventory_Management.py:1331-1334`). On a
shared list, leaf A's drain resetting the pool before leaf B records is a silent, plausible-looking
zeroing. So on a coupled run the manager drain stops resetting, and **the pool resets the one list
once per site day, after both leaves have drained**. Giving that trap a single owner is the main
argument for the module in section 5.

### 2. The uid allocator: one crew, `first_uid = max(k_pickers)`

Today each arm builds `_put_crew.workers(_pick_crew.next_uid(0))` (`strategy_runner.py:987-992`),
which under coupling produces **two** defects, not one:

- each leaf fields the full derived site crew — the double count, which ticket 02 fixes by
  deletion (site crews move to unit scope);
- because `k_pickers` differs per channel, the same physical putter gets uid `k_store` in the store
  DB and `k_ful` in the fulfillment DB, so any site-level rollup joining on `actor_uid` merges two
  different people.

**One `_Crew(PUT, size=derived.put.crew)` at unit scope, one `workers(...)` tuple, both leaves.**
Its block starts at `max(k_store, k_ful)` so it clears both channels' dense picker uids (every
existing analysis assumes `actor_uid == picker_id` for pickers). The leaf with fewer pickers then
has a **gap** in its uid space — harmless, because nothing reads uids densely (`put_rows`
bounds-checks `widx` against the roster; `work_events` has no uniqueness constraint) — and in
exchange the putter's uid means the same person in both DBs. A dense-but-lying uid is worse than a
sparse-but-true one.

The crew size must ride `workunits._shared` into the spawned worker (memory
`config-knob-has-five-seams`), or every worker silently rebuilds the declared default.

### 3. The day's division: proportional sub-deadline, then a residue pass

With shared clocks and a fixed leaf order, store drains first, pushes the clocks, and fulfillment
starts from wherever store left them — so **fulfillment would absorb every cut, every day**, as a
pure artefact of loop order. That bias would look exactly like a finding.

**Each leaf gets a sub-deadline at its own share of the day; then both re-drain against the full
day deadline.** The second pass *is* the charter's "finishes early releases labour to the other",
and it runs in both directions.

**The shares need no new record field.** `derived.put.expected_utilization[channel]` is
`load_ch / (crew x S)` by construction (`staffing.py:663-672`), so the share is
`exp_store / (exp_store + exp_ful)` — crew and day cancel. A single-channel catalogue gets share
1.0 and the residue pass is a no-op, so the degenerate case is correct for free.

**Rejected: first-come-first-served** (honest greedy, but the bias is real and systematic) and
**alternating leaf order by day** (kills the bias, but makes the cut alternate for no modelled
reason).

### 4. The cut: reused unchanged, counted once

Exhaustion is **not a new state**. `put_deadline` is a START gate on a remainder
(`crew_clock.can_start`: `min(clocks) < deadline`), and a leaf sees today's cut — arriving earlier
because the other channel already spent the clocks. No new counter.

What changes is that `cut` now has two possible causes (my day ran out / the site's crew ran out)
and nothing on the row distinguishes them. That is acceptable; what is not acceptable is summing
it (memory `cut-is-a-level-not-a-flow`: `cut` resets per batch but re-counts the standing queue,
which published a 101x number once already). The additive statistic stays "count of batches where
it was non-zero", and **the per-leaf split is kept** — a site total would hide exactly the
asymmetry section 3 exists to prevent.

**The residue pass would double-count it unless the count moves.** `_stock` charges
`queue.cut += len(queue.items)` in a loop at the end of the call
(`Inventory_Management.py:1488-1491`); with two passes, pass 1 charges a cut against a queue pass 2
then serves, and because `cut` is a level the inflation lands *inside* one batch, where no
"non-zero batches" rule can undo it. So **extract that loop as a public `count_put_cut(deadline)`**
and have the pool call it once per leaf, after the residue pass, against the full-day deadline.
Flag-off, `_stock` keeps calling it inline, so the extraction is byte-identical.

### 5. Where it lives: `Inbound/putaway_pool.py`, and a third port

The import boundaries settle this. `wh_operations -> wh_inventory` is forbidden
(`architecture.yml:88`), so the package where crews live cannot drive a manager's drain;
`Warehouse -> Inbound` is forbidden both ways. **`Inbound/` is the only package that may sit above
two managers**, which is why `SiteReceiving` went there (01).

`Inbound/putaway_pool.py` owns the shared list, the proportional split, both passes, and the
once-per-site-day reset, duck-typing two manager-shaped leaves. Phase 5 is promoted from
`_drain_putaway` to a public `drain_putaway(deadline)` — **01's two ports become three**. The
phases were designed for exactly this: `inventory_reorder.py:493-498` says they were separated so
"a second work stream (inbound put-away against the same clock) needs to interleave these".

**Rejected: a second port-set on `SiteReceiving`** (fuses two genuinely different crews into a
coordinator 01 scoped to the dock, the yard and the drain record) and **inline in the batch loop**
(~15 lines, untestable except through the whole worker, in the file everything already accretes
into).

### 6. One crew, two prices — stated and tested

`_PutawayCost.from_pick` builds the put price from **this channel's** pick config
(`strategy_runner.py:1000-1006`), and the staffing derivation is emphatic that `s_put` is keyed by
channel: "a site-wide put price is what this derivation used to get wrong, and there is no longer
anywhere to put one" (`staffing.py:694-697`).

So the two leaves keep **two per-unit put prices over one crew**. It composes mechanically
(`charge(dur)` has no opinion about where the duration came from) and it is defensible — the price
is a property of the work's geometry and handling, not of the person. But it quietly asserts that
the same putter works at two rates depending on whose pack is on the cart, which is the class of
thing the charter requires be stated and tested rather than assumed (as it did for the gain's
commensurable hours).

**Test:** charge one pack of each channel to the same pool and assert the two durations differ by
the ratio of the two `s_put` constants — so the day a site-wide price sneaks back in, something
fails.

### 7. What the band reads: one site number

`expected_utilization`'s caveat that "integer crews and single-channel leaves undercut rho by
construction" (`staffing.py:663-667`, `equilibrium.py:37-38`) exists **only because of the double
count**. Coupling removes the cause.

Because `expected_utilization` is linear in load, the two leaves' recorded expectations sum exactly
to the site expectation. So the coupled put clause reads **one site number**: both leaves'
`_put_seconds` over `crew x day_seconds x days`, banded against the summed expectation — which now
sits at rho rather than below it. The caveat is retired for put on coupled runs and kept verbatim
flag-off.

Keep the per-leaf realized numbers visible beside it: they are how section 3's fairness rule is
seen working. The same argument applies to receiving, but that is 01's crew — noted here so ticket
07 inherits both. **This ticket owns the number; 07 owns where it is reported.**

### 8. Flag-off, and two refusals

**The switch is the unit's own shape** (`uid[1] == 'coupled'`). A coupled unit carries the put crew
at unit scope and the leaf builder binds both managers to the one shared list; a flag-off unit
carries `put_crew` in its leaf payload exactly as today and `bind_crew` builds its own clocks,
double count and all. The off-state is **structural** in the `recv_crew_spec` style: the pool is
*not constructed*, rather than constructed and bypassed.

**Equivalence test:** a store-only run and a flag-off two-channel run produce byte-identical
`work_events` and `put_queue_state` rows against a pre-change baseline.

Two loud refusals, both matching 01's standing-yard-only precedent:

1. **A coupled unit with the working-day grid off.** The shared pool needs one site day boundary to
   be denominated in anything.
2. **A coupled unit with `put_queues` non-None.** `PUT_QUEUE_SPLIT` (`settings.py:406`, off by
   default and off in every campaign config) gives one manager three queues each with its own crew,
   precisely so "a forklift crew and a cart crew are not the same people"
   (`Inventory_Management.py:1130-1135`). A coupled run already partitions by channel, so the
   split's fulfillment queue would sit empty inside the store leaf; and one shared list across six
   queues would fuse three crews the split exists to keep apart. The two features are two answers
   to the same question, not a flag combination.

### 9. Amendment to ticket 02

**`put_clock` becomes site-wide.** 02 decided "`recv_clock` becomes site-wide; `arm_clock` and
`put_clock` stay per leaf... regardless of what ticket 04 decides about the shared **budget**". That
hedge assumed a shared budget; a shared **clock list** cannot have two epochs — `put_clock` is the
absolute carry `crew_start` is measured from (`strategy_runner.py:1765`, `work_events.put_rows`), so
two carries over one list stamp the same worker's same second at two different absolute instants.

The amendment makes put exactly parallel to receiving, on 02's own reasoning: "under one dock there
is one such carry" — under one put crew there is one put carry. Segregated *volume* does not imply
segregated *people*, and the carry belongs to the people.

**The base is the site day start**: `max(day_start(i), put_clock_site)`.

**Rejected: `max(batch_start_store, batch_start_ful, put_clock_site)`.** Strictly causal, but it
idles the site's putters whenever *either* pick crew overran its day (`release_at` clamps the late
arm later), writing rows that say the putters waited on the slower pick crew — untrue of a site
whose putters start at shift start and work what is on the dock, and it would misattribute a
picking overrun to put-away's cut. The queue's **contents** are correct under either base, because
02's loop order already runs both leaves' phases 1-3 before the drain; only the timestamp differs.

### 10. Two confirmations that dissolved

- **The source stamp is unaffected.** `_admit` stamps `source` from
  `getattr(self.transit, 'SOURCE', 'reorder')` (`inventory_reorder.py:481`); under a shared transit
  both leaves read the same object and get the same string — but `source` names the *transit kind*
  ('yard' / 'trailer' / 'reorder'), never a channel, and under one site yard both leaves genuinely
  are fed by the same kind. Same string, correct.
- **`carryover-two-producers-one-key` does not recur.** The table is
  `PRIMARY KEY (run_id, batch_id, reason, sku)` and each leaf writes **its own sim DB**; ticket 03
  settled that only trailer- and door-denominated rows go to `_site/`, so nothing put-denominated
  lands where two leaves' keys could meet. **Assert the two leaves' `run_id`s differ**, so the
  "different DBs" premise is checked rather than assumed; `_insert_carryover`'s raise (`3a8b4ab`)
  stays the backstop.

### What this ticket hands onward

- **Ticket 07** inherits section 7: the coupled put band is one site number against the summed
  expectation, and the receiving clause takes the same shape from 01's crew.
- **Ticket 02** carries the section 9 amendment as a note.
- **Two byte-identical precursors** graduate into a build ticket:
  [Seat the put-pool injection seams](12-seat-the-put-pool-seams.md).
