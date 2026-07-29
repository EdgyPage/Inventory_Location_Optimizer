"""Optimization.persistence — SQLite schemas and read/write for a run's DBs.

Leaf modules by design: none of them imports anything else in Optimization, so persistence can
never drag configuration or orchestration into a worker.  See README.md.
"""
