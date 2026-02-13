"""Nested functions and class declarations inside a function."""

# Baseline (this file): references=10 classes=1 nodes=10 outgoing_edges=9 incoming_edges=9


def outer(value: int) -> int:
    def inner_step(num: int) -> int:
        return helper_nested(num)

    class LocalHelper:
        def compute(self, amount: int) -> int:
            return helper_nested(amount)

    helper = LocalHelper()
    return inner_step(value) + helper.compute(value)


def helper_nested(num: int) -> int:
    return terminal_nested(num)


def terminal_nested(num: int) -> int:
    return num + 1
