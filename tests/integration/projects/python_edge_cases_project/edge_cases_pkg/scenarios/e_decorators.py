"""Decorator wrapper and wrapped function calls."""

# Baseline (this file): references=9 classes=0 nodes=9 outgoing_edges=6 incoming_edges=6


def logger_decorator(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@logger_decorator
def decorated_action(value: int) -> int:
    return normalize(value)


def normalize(value: int) -> int:
    return value * 2


def run_decorator() -> int:
    return decorated_action(4)
