# The source fingerprint cannot see a file nobody has written yet

Type: task
Status: resolved

`SHAPE_SOURCES` is a tuple of file PATHS, and its own comment states the stakes:

> Keep this list generous -- a false trigger costs one canary pair, a missing entry costs a
> silently-broken downstream tool.

It already carries one recorded scar: `run_whatif_volume.py` "declares two run-root artifacts and
was never listed here, so editing it did not move the source fingerprint."

## The defect

A tuple of paths can only ever name files someone already thought of. One package turns a file
nobody thought of into a **tree change**:

* `Optimization/simconfig/configs/` is auto-discovered. `core/discovery.py` says so in its own
  docstring: *"dropping a new pick-config file into configs/ registers it with zero edits
  elsewhere."*
* A registered pick-config's **NAME becomes the `<config>` LEVEL's directory name.**

So adding a pick-config renames a level of the run tree, and `source_fingerprint()` sees nothing.
Preflight never re-proves the shape, and the new directory appears unannounced.

**Listing today's four files would not have fixed it** -- the fifth would be exactly as invisible.
That is the whole point, and it is why this needed a mechanism rather than four more lines.

A second, ordinary omission: **`Optimization/config/channels.py`** decides whether the CONDITIONAL
`<channel>/` level exists at all (it appears only on a mixed catalogue). As shape-defining as
`sim_config.py`, and absent.

## What landed

`SHAPE_SOURCE_DIRS`, hashed after `SHAPE_SOURCES`: for each directory, the **sorted `*.py` name
list first, then each file's content**.

The name list is hashed *before* any content deliberately. Two files with identical bytes under
different names are the same content and a **different run tree**, because the name IS the
directory. Content hashing alone would miss a rename; this does not.

Plus `Optimization/config/channels.py` in `SHAPE_SOURCES`.

## Acceptance

Four tests in `Tests/integration/test_runschema_contract.py`, each against a synthetic repo root
so nothing touches the real `configs/`:

* an ADDED pick-config moves the fingerprint;
* a RENAMED one moves it **though no byte changes**;
* the hash is STABLE when nothing moves (or the two above pass for free, and the mechanism becomes
  a false-trigger machine costing a canary pair per call);
* `channels.py` is in `SHAPE_SOURCES`.

**Proven non-vacuous**: with `SHAPE_SOURCE_DIRS` forced back to `()` -- the pre-fix state -- the
add and rename tests both fail with their own messages.

Gates: `Tests/integration/test_runschema_contract.py` 52 passed; `contract --check`,
`verify_context.py`, `path_guard` all exit 0. The hashing ALGORITHM changed, so the fingerprint
moved by construction; full `preflight` ran both canaries and reported **tree shape UNCHANGED --
schema 341e1422457c still valid**.

## Note for whoever adds the next auto-discovered package

`Performance_Evaluations/core/discovery.py` mirrors this pattern for the evaluation registry. Its
outputs are figures and tables, whose FAMILY names are declared in `runschema/schema.py`
(already a shape source), so an added evaluation does not by itself rename a level. If that ever
stops being true, it belongs in `SHAPE_SOURCE_DIRS` for the same reason `configs/` does.
