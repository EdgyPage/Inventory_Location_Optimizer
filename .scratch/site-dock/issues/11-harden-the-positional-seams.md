# Harden the three positional seams

Type: task
Status: resolved

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

## Amendment — a fourth seam (site-dock 07, 2026-09-11)

`Optimization/Performance_Evaluations/core/requests.py:601` reads
`scope = ev.scope if ev.scope in ('aggregate', 'run') else 'config'`, so an evaluation scope this
module does not recognise is **silently namespaced as `config`** rather than refused. Same class as
the unvalidated `registry.evaluation(scope=...)` string already folded in here, same silence, and
about to matter: `site` is a fifth scope. Fix the two together — validating the string at
declaration is worthless while a consumer quietly reinterprets it.

## Amendment - a fifth seam (site-dock 10, 2026-09-11)

`Optimization/simdriver/supervisor.py:108-110` attaches `expected_pick` with
`meta[gk]['sim_skeleton']` where `gk = uid[:3]` (`:86`), matching `_s.get('key') == uid[3]`.
[Design the coupled work unit](02-design-the-coupled-work-unit.md) settled `uid[:3]` for the
**finalize** path by carrying `group_keys`; this block slices the uid **independently and
earlier**, so that decision does not reach it. Under the coupled uid
`(label, 'coupled', arm_store, arm_ful)`, `meta[gk]` raises **KeyError** — inside the success
`try`, so a unit that SUCCEEDED is logged `strategy FAILED`, added to `failed_uids`, and never
finalizes either leaf.

Unlike the other four this one is **not silent**: it is loudly mis-attributed, which is its own
failure mode — a reader scanning `run.log` sees a failing arm and goes looking for a simulation
bug that does not exist. Fix it the same way: take the group key and the arm key from the payload
rather than by slicing, alongside the `group_keys` list 02 already adds.

Byte-identical today for the same reason as the rest: on a flag-off unit `group_keys` is
`[uid[:3]]` and the arm key is `uid[3]`.

## Answer

**BUILT and live on `develop`** — three commits for five seams, plus one for the derived state:
`96adb0e6` (reserved prefix), `081ea109` (uid identity), `4066071a` (evaluation scope),
`8a9d8f8e` (fingerprints + architecture layer).

The ticket's "three separate commits, one per seam, since they share a reason but no file"
survived its premise but not its arithmetic. The two amendments added seams 4 and 5; seam 5's fix
lands in the same two files as seam 2's — both are "the supervisor's success path stops reading a
uid positionally" — while 4 is worthless apart from 3, as the amendment itself says. So: prefix /
uid / scope, three commits by reason rather than by seam count.

### 1. The reserved prefix — and the walker that never read it

`RESERVED_PREFIX` was declared, hashed into the contract, and read by **no walker**; all three
skips were hardcoded `_` literals at the **pair** level only. `runlayout._reserved` now reads the
declaration and every walker applies it at the pair, config AND channel depths (`cells`,
`iter_channel_runs`, `iter_sim_dbs`). The two other literals went the same way —
`preflight._generalize_file` and `artifact_map._rel_dir`.

**The ticket's third path does not exist.** `runschema/artifact_map.py:68` is
`Optimization/Performance_Evaluations/core/artifact_map.py:68` — re-resolved, same line, same
`parts[0].startswith('_')`. Its named `_frozen` / `_viz` / `_aggregate` branches (and
preflight's) are NAMES, not the prefix, and stay.

**Byte-identity is structural here, not merely tested:** the contract declares no `_`-prefixed
DIRECTORY at the config or channel depth — only the `_ckpt_*.pkl` and `_batches_*.pkl` FILES,
which every walker already excluded with `os.path.isdir`. Nothing on disk today could take the
new path.

### 2. The uid — and a fourth spelling of "no channel" it uncovered

`record_arm`'s four key fields are KEYWORD-ONLY. A caller holding a differently-shaped uid now
gets a `TypeError` at the call site instead of writing `channel = <arm_store>` into a
`TEXT NOT NULL` column whose IntegrityError the supervisor swallows.

Seam 5 (amendment 2) needed the payload keys 02 promised, so `workunits._stamp_identity` adds
them: it stamps `group_keys` + `arm_key` and returns the uid, and `_run_pool` reads those.

**One decision the ticket did not contain, taken here.** A unit finalizing several leaves has one
expected day PER leaf; a single `res['expected_pick']` copied into both would put one leaf's day
on the other's arm. That is a NEW silent wrong answer, so the multi-leaf case is **refused with a
warning** rather than fabricated — the value's shape stays the coupled unit's to settle. The
alternative, inventing a per-leaf dict contract here, would pre-empt 02's build.

**The fourth spelling was three sites, not one, and two were not in the ticket.**
`run_map_precompute` spelled it `run.channel or ''` at `:179` (the runtime LOOKUP), `:202` (the
`WHERE channel=?` UPDATE the ticket names) and `:209` (the census CSV column). The lookup matters
as much as the update: it keys `_recorded`, so a store arm's identity gate was reading a row it
could never find. The rule moved onto `ChannelRun.channel_key` — the dataclass that owns the
ambiguity, beside `group_key` — and `run_whatif_delta._channel_of`, imported by three what-if
writers, delegates to it. `channel_key` is total by construction: a non-mixed catalogue yields
**store** runs only (`workunits._channel_runs_for`), so an absent `<channel>/` level means
`store`, never "no channel".

This is the one **behaviour change** in the ticket, and only for `run_map_precompute`'s backfill
path, which previously wrote nothing at all on a store-only layout and logged
`(no row to update)` while doing it. No simulation output moves.

### 3. The scope string — validated AND given a namespace

`registry.SCOPES` is now the declaration; the decorator raises on anything else.
`requests.REQUEST_SCOPE` replaces `... else 'config'` with a map that is **total over SCOPES**
(an import-time assertion keeps it total), and `resolve_needs` raises rather than guessing.
`per_strategy` and `config` keep one shared namespace — two evaluation scopes over the same
context kind, which is what made the fallback look harmless.

`site` is minted here as the fifth. Nothing declares it and no stage schedules it
(`driver._CONFIG_SCOPES`, `aggregate_keys` and `run_keys` all filter by exact scope), so today it
is inert — which is the point: the map has to be total **before** something declares it.

### Acceptance

| asked | result |
|---|---|
| output tree byte-identical, store-only and mixed | **preflight canaries** — two full runs through the real driver and pool: `tree shape UNCHANGED — schema 5c9bc35db55b still valid` |
| a reserved dir at config + channel depth yields no ChannelRun and no sim DB | `Tests/integration/test_reserved_prefix_walk.py` (3 tests) |
| a coupled-shaped uid reaches `record_arm` with the right four values | `test_key_fields_are_keyword_only` plus `test_a_coupled_shaped_uid_succeeds_instead_of_being_logged_as_a_failure` |
| an unknown evaluation scope raises at decoration time | `Tests/unit/test_evaluation_scope.py` (5 tests) |
| separate commits | three by reason — see above |

Every new guard was **mutation-checked**: reverting each fix fails the test that claims it
(memory `real-test-coverage-is-317` — a pass count is not proof). The plants in the prefix test
are full marker-carrying, sim-DB-bearing leaves, and a third test renames the prefix off two of
them to prove the walkers DO pick them up, so the exclusion is by prefix and not by a malformed
fixture.

### Gates

2015 unit, 403 integration + 1 skipped, 31 e2e + 1 skipped, all green. context, architecture,
site `--fast`, run-tree contract + preflight, profiles-tree, DB-shape `--adopt`, memory, path
guard and docref guard: all OK.

`Tests/architecture`: **4 failed, 378 passed, 1 skipped** — the *same four*
[Extract the one-leaf receiving coordinator](09-extract-the-one-leaf-coordinator.md) attributed,
re-attributed here the same way (`git archive HEAD | tar -x`, memory `head-copy-via-git-archive`):
`test_key_backbone_edges_present`, `test_the_dead_site_is_still_dead` and
`..._conditional_table_is_declared` fail on clean HEAD; `..._requirements_is_validated_here` fails
only in the working tree, because it walks two `.claude/worktrees/` checkouts of other branches.
None are this ticket's. Two are now cheap to name precisely, so they are written down rather than
left as "pre-existing": the dead-site scan matches `StorageCart.add_from_bin` in
`Inbound/trailer.py`'s **docstring and comment**, not a call; and the undeclared conditional
loader is `_shift_day_select` (`Optimization/persistence/Picking_Data.py:1631`).

### Two findings

**The architecture layer was stale before this work.** `arch-synced-commit` read `20ba2fcd` while
HEAD carried `8a5b4f42` — site-dock 09's `SiteReceiving` extraction, a code commit that was never
synced. So `8a9d8f8e` syncs that as well, and the marker moves to `4066071a`. Worth knowing for
the map: **09 left the layer un-synced**, and every build ticket after it inherits the same
obligation. The maintainer agents do not commit, so an un-run chain is invisible until the
verifier is run by hand.

**A catalogue purpose is seeded from a docstring FRAGMENT.** `--catalog-merge` takes the first
docstring line verbatim, so all three new test modules landed as sentence fragments ("the
reserved prefix is DECLARED once...") rather than the `Locks the ...` form every neighbouring
test entry uses. It is NOT a `purpose: TODO`, so CLAUDE.md §1's "fill every new TODO here" step
does not catch it — the drift is silent and only a reader notices. Hand-filled.
