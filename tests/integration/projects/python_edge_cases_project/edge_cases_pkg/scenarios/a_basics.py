"""Top-level function calls and cross-module resolution."""

# Baseline (this file): references=4 classes=0 nodes=4 outgoing_edges=2 incoming_edges=2

from edge_cases_pkg.scenarios.cross_module import external_target


def alpha() -> int:
    return beta()


def beta() -> int:
    return gamma()


def gamma() -> int:
    return 11


def call_cross_module() -> int:
    return external_target()
