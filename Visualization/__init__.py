"""Visualization — the spatial run viewer.

A package so `python -m Visualization.precompute` works and the reader layer can be imported
package-absolutely.  See README.md for what belongs here; RECONSTRUCTION.md for what the
persisted data can and cannot answer.

Nothing is imported eagerly: `server.py` needs flask, `precompute.py` must NOT, and the readers
need neither.  Importing this package pulls in nothing but this docstring.
"""
