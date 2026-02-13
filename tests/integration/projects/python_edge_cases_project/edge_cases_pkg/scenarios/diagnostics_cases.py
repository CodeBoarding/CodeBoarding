"""Symbols intentionally left unused to produce diagnostics."""

# Baseline (this file): references=8 classes=1 nodes=8 outgoing_edges=1 incoming_edges=1

import math

UNUSED_CONSTANT = 100


def unused_function(value: int) -> int:
    temp = value + 1
    return temp


class UnusedClass:
    def method(self) -> int:
        return 0


def unused_with_parameter(unused_argument: int) -> int:
    return 42
