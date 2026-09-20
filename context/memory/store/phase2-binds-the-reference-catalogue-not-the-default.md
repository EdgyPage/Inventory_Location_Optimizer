---
name: phase2-binds-the-reference-catalogue-not-the-default
description: "Any phase-2 run must be launched with --profiles-dir pointing at the catalogue_reference_lt0 tree; the default profiles dir's newest run is a perf catalogue the staffing pin refuses"
metadata:
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-20T07:06:15.463Z
---

`PHASE2_STAFFING_PIN` names the catalogue pair phase 1 ranked. A phase-2 spec that carries
the pin (`inbound_unload`, `_probe_unload_ref`) refuses any other pair, loudly:

    RuntimeError: [staffing] this run carries a campaign staffing pin, and it does not name
    the pair '<...>' ... Phase 1 ranked the pairs it ran; a pair phase 2 adds afterwards was
    never ranked.

The trap is that the refusal is about the DEFAULT. `--profiles-dir` defaults to the
`catalogue` tree under `PROFILE_INPUT_DIR`, and that tree's NEWEST run is the 40k perf
catalogue the toy and bench specs use. Phase 1 and phase 2 both ran against a SIBLING tree,
`catalogue_reference_lt0`, which holds exactly one run -- the pinned one. So a phase-2 run
launched without `--profiles-dir` binds the perf catalogue, dies at the pin, and reports
every unit unrecovered (which it does correctly -- see
[[pool-run-swallows-dead-arms]] -- but four minutes in, not at argument parse).

**Launch a phase-2 run with `--profiles-dir <PROFILE_INPUT_DIR>/../catalogue_reference_lt0`
and `--profile-run` naming the pinned run.** `run_spec.json` of any finished phase-2 root
records both, so the right values are always recoverable from the last campaign rather than
remembered -- and [[results-drive-location]] applies: resolve through the .env key and
verify, never a drive letter.

The campaign left `--profile-run` null and took the latest under that dir. Pin it anyway:
the dir holds one run today, and a second one appearing would move the campaign silently.
