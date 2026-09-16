# A scan keyed on a C callable is invisible to the offender table

Type: research
Status: resolved

Found by running the two new assignment-family cells side by side and reading what they did NOT
say.

## The two cells run the same pool and report different worlds

| | `ranked_tmin` | `ranked_popularity` |
|---|---|---|
| `_RankedAssignPool.take` | k = 1.018, counts `[4191 … 76514]` | k = 1.018, **identical counts** |
| the per-placement aisle scan | **absent from the offender table** | k = **1.963**, 8,819,328 calls |

Same pool, same takes, same wave. The only difference is what the selector's KEY is made of:

    tmin        min(head_D, key=head_D.__getitem__)                    # a C method
    popularity  min(head_D, key=lambda aid: (ads.get(aid,0.0), ...))   # a Python lambda

Both scan every live aisle on every placement. Only one of them is counted.

## Why

`calltree_tracer` turns every `c_call` into a node with `kind='ext'`
(`parent.child(name, kind='ext').calls += 1`), and `calltree_growth._flat_counts` skips exactly
those:

    def walk(node):
        for c in node.get('children', []):
            if c.get('kind') == 'ext':
                continue

So a scan whose key is `dict.__getitem__` is counted as an external leaf and then **dropped
before the offender table is built**. A scan whose key is a Python lambda is a project node and
gets convicted.

The exclusion is not wrong in itself -- its stated reason is that thread-pool bookkeeping varies
with OS scheduling, so C leaves would make `counts_fingerprint` non-deterministic. But its
consequence has never been stated: **the count instrument systematically under-reports the
cheapest-to-write form of the most common superlinear shape in this codebase.**

## What it means for reading any offender table, including this round's

`tmin` is NOT cheaper than `rank_popularity`. Its scan is the same width -- 115.3 aisles per take
at 8,000 SKUs -- and costs less only insofar as `dict.__getitem__` is faster than building a
tuple. An offender table showing one and not the other invites exactly the wrong conclusion, and
this effort came within one cell of drawing it: ticket 04 recorded `_RankedAssignPool` as
"unmeasured, not acquitted" on the basis of a table that would have stayed silent about `tmin`
even with the cell present.

**So `t_sample` was not the only place this instrument described something other than what
production runs.** That one was a fixture divergence; this one is structural, and no `--config`
fixes it.

## Not fixed, and why

Counting C leaves would restore the non-determinism `counts_fingerprint` depends on, which is a
worse trade than the blind spot. Two cheaper mitigations, neither attempted here:

1. **Report `ext` counts in a separate block** -- excluded from the fingerprint and from the
   offender ranking, but printed, so a 8.8M-call `dict.__getitem__` under a `take` is at least
   visible to a reader.
2. **Fit the SCAN WIDTH directly.** `selector-lambda / takes` recovered "aisles per take" here
   without any of this machinery, and it is the quantity that actually matters. A per-placement
   ratio over a counted parent is immune to what the key is written in.

Recorded so the next reader of an offender table knows what it cannot say.
