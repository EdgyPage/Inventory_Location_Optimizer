---
status: accepted
date: 2026-09-11
---

# Inbound scope splits at the pack: pack-denominated receiving is a channel's, trailer-denominated inbound is the site's

Under one site dock both channels' freight arrives on one trailer, through one door set, worked
by one receiving crew. The obvious reading is that receiving therefore became a site quantity and
stops being a channel's, which would make every receiving column in a channel's own database a
lie. That reading is wrong, and getting it wrong would have moved a dozen honest columns into a
file nothing needs them in. **Packing partitions by channel before it packs, so every pack leaving
the dock has exactly one owning channel.** The pack is where inbound stops being shared.

So the split runs at the pack, not at the dock. A quantity denominated in packs, or in the labour
of handling packs, decomposes to an owning channel and stays in that channel's own record. A
quantity denominated in trailers or doors has no owning channel at all, because a trailer's load
is mixed by construction and a door is occupied by the trailer rather than by either channel's
share of it. Those move to a site record that sits above both channels.

| Stays with the channel | Belongs to the site |
|---|---|
| receiving depth, unloaded, cut, seconds | trailers, their arrival, staging and emptying stamps |
| repack counts | door counts, door spans, door utilization |
| receive-role work events | yard depth at each drain |
| dock carryover and dock queue rows | detention, overage, the fee threshold |
| standing dock depth per working day | the receiving crew's own denominator |

One consequence is not a storage question and deserves naming here: **a leaf's receiving seconds
divided by the site receiving crew is not that channel's utilization.** It is a share of a whole
the channel does not own. Every such read-out is a site quantity even though its numerator
decomposes, and it moves with the site half of the table above.

## Considered options

- **Receiving is wholly site-scoped.** Every receiving column leaves the channel record for a
  site one. Rejected: it discards a real decomposition the charter already guarantees, and it
  would make the commonest questions — what did fulfillment's inbound cost, how deep did the
  store's dock queue run — unanswerable from the record that answers every other question about
  that channel.
- **Receiving stays wholly channel-scoped, one elected channel carrying the shared rows.** Zero
  schema work. Rejected: it reproduces the per-leaf artefact the coupling exists to remove, and
  it renders a yard scorecard for one channel and an empty one for the other, which reads as a
  finding rather than as an artefact of who was elected.
- **Receiving stays wholly channel-scoped, both channels carrying a copy of the shared rows.**
  Rejected: two copies of one yard sum to twice the trailers, and the summing consumer is the
  common case, not the exotic one.

## Consequences

- The site half lives in its own database beside the two channel records, under the one directory
  that dominates both of them. That is a home, not a level: it is declared the way the run-scope
  dossier tree is declared, so it costs a contract bump and no change to the tree's shape.
- The channel stamp on a channel's own record stays honest and needs no sentinel, because the
  rows that stay are genuinely that channel's. The only sentinel minted is on the run-timing
  record, where one coupled process legitimately produces one measurement for two channels.
- A receive-role work event is stamped on the site clock while a pick or put event beside it is
  stamped on its own channel's clock. One column, two origins, discriminated by role. The
  standing caveat that a cross-channel timeline is fiction narrows rather than disappears: it
  stays true of picking and putting, and stops being true of receiving.
- Analysis gains a fourth scope, the site, seeing both channels of one warehouse under one arm
  pair. The yard reporting family moves into it.
- This is reachable only with inbound coupling on. With it off, every quantity above is a single
  channel's by construction and nothing in this record applies.
