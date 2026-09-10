# Band the own-bin share and the free-index depth

Type: grilling
Status: open

Graduated 2026-09-09 from
[Re-check the reference pair under the first-time guarantee](31-recheck-under-the-first-time-guarantee.md),
which read the two instruments under the solved floor. HITL. Skills: `grilling` +
`domain-modeling`.

## Question

The rework clause REPORTS the own-bin share and the free-index depth and judges neither
("Build the empty-first top-up", 24): no steady state had been observed. Three readings now
exist, all fifo: the 2026-09-08 pair at the 1.0-line floor (own-bin share exactly 0.000 on
every day of both leaves; free index never below 1,197,833 of 2,096,050 bins, 57%) and the
2026-09-09 pair at the solved 1.27-line floor (0.000 again; floor 1,450,954 store / 1,399,264
fulfillment of 2,466,650, 59% / 57%).

Under ADR-0003 a top-up consolidates into the SKU's own bin ONLY when no empty bin fits, so a
non-zero own-bin share says a size bucket's free index ran dry -- the same finding the repack
term already fails on, one step earlier. Decide:

1. Is the own-bin share's band **exactly zero** (any top-up is a sizing finding, judged like a
   repack), or a declared tolerance (some buckets legitimately run tight)?
2. What free-index depth does the era declare -- a floor as a fraction of bins (the readings sit
   near 57-59% with 60 store buckets and 3 fulfillment buckets), per bucket or in total -- and
   is it judged or only reported? The put-away pricer assumes a free bin is always found
   (`s_put`, no search term); a floor is where that assumption is defended.
3. Whether either reading must be taken PER BUCKET rather than per leaf: a 3-bucket section can
   run one bucket dry while the total stays at 57%.

## Done when

- Both instruments have a declared band (or a recorded decision to keep one as a report), the
  rework clause judges accordingly, and its docstring names the readings the band was drawn
  from. A sabotage test proves each new term can fail.
