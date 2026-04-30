"""Persisted analysis artifact layer.

Owns the on-disk ``analysis.json`` schema, the EASE encoding, and the
locked read/write store. Both the engine (``diagram_analysis``) and the
incremental pipeline depend on this; this package depends on neither.
"""
