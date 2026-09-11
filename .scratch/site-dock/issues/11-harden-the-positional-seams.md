# Harden the three positional seams

Type: task
Status: open

AFK. The execution override (map Notes) graduating three findings out of
[Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md). No decision
is open here; all three are settled in that ticket and this one adds none.

**All three are byte-identical today** — nothing yet writes into a reserved directory below the
pair, no uid yet has the coupled shape, and every declared evaluation scope is already legal. Each
becomes a silent wrong answer the moment coupling lands, which is why they go in first and alone.

## Question

Three seams consume a value positionally, or accept one unchecked, and each is about to be handed
a shape it was never written for.

1. **The reserved-prefix guard exists at one tree depth only.**
   `Optimization/runschema/runlayout.py:144` (`iter_channel_runs`) and `:176` (`iter_sim_dbs`) skip
   `_`-prefixed directories at the **pair** level; the config loop (`:146-149`) and the channel
   loop (`:153-156`) have none. So `<pair>/_site/` — the home ticket 03 chose — is walked as a
   phantom **config**, and any `sim_*.db` beneath it surfaces as an extra **arm**.
   `RESERVED_PREFIX` (`runschema/schema.py:59`) is declared and hashed but read by **no walker**;
   all three skips are hardcoded `_` literals. Make the walkers read the declared prefix, and
   apply it at every depth. Check the other two literal sites while you are there:
   `runschema/artifact_map.py:68` and `preflight.py:230`.

2. **`record_arm` unpacks a variable-shape uid positionally.**
   `Optimization/simdriver/supervisor.py:114` passes the work-unit uid whole;
   `Optimization/persistence/runtime_metrics.py:156` does
   `pair, config, channel, arm = uid`. The coupled uid from
   [Design the coupled work unit](02-design-the-coupled-work-unit.md) is
   `(label, 'coupled', arm_store, arm_ful)` — same arity, different meanings — so the first
   coupled run silently records `channel = <arm_store>`. `channel` is `TEXT NOT NULL` and the
   supervisor swallows the resulting IntegrityError with a warning (`supervisor.py:115-116`), so
   neither the wrong value nor a rejected row would announce itself. Take the four fields by
   keyword at the call site. While in this file, fix the pre-existing fourth spelling of "no
   channel": `Optimization/run_map_precompute.py:202` keys `WHERE channel=?` with
   `run.channel or ''` where `record_arm` wrote `store`, so that UPDATE matches nothing today and
   logs `(no row to update)`.

3. **An evaluation's scope string is validated by nothing.**
   `Optimization/Performance_Evaluations/core/registry.py:22-31` documents
   `per_strategy | config | aggregate | run` in a **comment**; the `evaluation` decorator
   (`:116-118`) checks no value. Ticket 03 mints `site` as a fifth. A wrong scope renders a family
   into the wrong tree with no error. Validate against a tuple and raise on an unknown value.
   Note the parallel vocabulary that is **not** in scope here: `core/families.py`'s
   `FAMILIES[*]['scope']` takes `leaf | run` and is a different axis.

## Acceptance

- A run's output tree is byte-identical before and after, on a store-only run and on a mixed one.
- A test proves each guard actually fires: a reserved directory planted at the config and channel
  depths yields no `ChannelRun` and no sim DB; a coupled-shaped uid reaches `record_arm` with the
  right four values; an unknown evaluation scope raises at decoration time.
- The three are separate commits on `develop`, one per seam, since they share a reason but no
  file.
