# Build the composite gain bundle

Type: task
Status: claimed
Blocked by: 21

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Design the composite gain bundle](05-design-the-composite-gain-bundle.md) settled the shape and
[Seat the one-owner bundle indirection](13-seat-the-one-owner-bundle-indirection.md) seated the
cursor and the provider protocol it hangs on — the evaluator already resolves arm machinery per
`BinKey` owner through `for_key`, and today's single-leaf run answers every key with one
instance. What is missing is a SECOND owner, which is
[Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md)'s
`{sku: leaf}` owner dict.

## Question

Build 05's answer: `SiteGainBundle` itself, the second `_gain_bundle_for` call, its refusal when
two owners' gate knobs disagree (13), and the three-part commensurability test **including its
sabotage**.

**The commensurability claim is the charter's, and it is stated so it can be falsified.** Gain
prices a mixed trailer per unit, keyed by owning channel, summed to one trailer score in hours.
The objective is already denominated in put + pick hours from the shared cost model with no
per-channel weighting (inbound-optimization decision 10), so the hours are commensurable by
construction — but that quietly decides a fulfillment hour and a store hour are worth the same to
the site. The charter requires that be TESTED, not assumed, which is what the third part of the
test is for.

**One fact 19 added that the design predates:** the two channels keep two per-unit put prices
over ONE crew, and that is now built and tested (`s_put` is keyed by channel and the crew is
not). So a per-unit gain keyed by owning channel is consistent with how the labour is actually
priced — the bundle is not introducing a per-channel asymmetry, it is reflecting one that the
cost model already has.

## What proves it

- **The commensurability test's SABOTAGE**: a planted per-channel weight must make it fail. A
  test that only passes on the correct code is the defect memory `a-count-is-not-a-claim` warns
  about one level up — divide by a denominator before concluding.
- **The disagreeing-gate refusal fires**, mutation-checked; 13 seated the indirection precisely so
  two owners with different gate knobs cannot silently resolve to one.
- **A single-owner run is BYTE-IDENTICAL** — `OneOwnerBundle` answers every key with the same
  instance today, so every seeded `fifo`/`lifo` arm and every single-channel gain arm must diff
  row for row to zero against a `git archive HEAD` copy. Count the gain-scored rows before
  trusting the diff: an unscored table diffs clean (memory `map-exact-solver-rarely-fires` is the
  same shape — a gate that rarely admits anything makes an empty comparison look green).
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).
