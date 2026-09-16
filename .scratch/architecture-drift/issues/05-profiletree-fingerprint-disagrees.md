# 05 - two source-fingerprint implementations disagree on identical input

Type: bug
Status: needs-triage

`Tests/architecture/test_profiletree_consumption.py::test_source_fingerprint_matches_the_runtree_implementation`

```
AssertionError: the two implementations disagree on identical input:
    profile_tree:        sha256:ee0e55db83539d85936fd2a8b8dd3fb95b57b4763fa7edfa0c20202faff3da0c
    runschema.contract:  sha256:96f1d570624b6e743bc8a520d3e9a9bea1ec3c1053c56d2f8080c338cc0f281c
```

`Schema.profile_tree` and `Optimization.runschema.contract` compute different fingerprints for the
same input. One of them has drifted.

**This is the dangerous one of the seven.** A fingerprint decides whether a run tree is readable and
whether a cached artifact is valid, so two implementations that disagree means one caller population
thinks a tree is current and another thinks it is stale, with no error either way. Fixing a contract
never rescues a finished run - so the longer this sits, the more artifacts are stamped with
whichever answer is wrong.

Likely one job with 04.
