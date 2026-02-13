"""Direct and mutual recursion scenarios."""

# Baseline (this file): references=5 classes=0 nodes=5 outgoing_edges=5 incoming_edges=5


def factorial(value: int) -> int:
    if value <= 1:
        return 1
    return value * factorial(value - 1)


def is_even(value: int) -> bool:
    if value == 0:
        return True
    return is_odd(value - 1)


def is_odd(value: int) -> bool:
    if value == 0:
        return False
    return is_even(value - 1)


def run_recursion() -> int:
    return factorial(5) + int(is_even(4))
