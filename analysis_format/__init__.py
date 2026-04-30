"""Wire-format peer for the analysis tree.

Owns the on-disk schema (``analysis.json``), the EASE encoding, and the
locked read/write store. Both the engine (``diagram_analysis``) and the
incremental pipeline depend on this; this package depends on neither.
"""
