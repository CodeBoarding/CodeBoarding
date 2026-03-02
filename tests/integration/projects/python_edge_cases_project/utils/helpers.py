# --- Static Analysis Expected ---
#
# Defined entities:
#   Functions (4): add, subtract, clamp, format_name
#   Constants (3): MAX_VALUE, MIN_VALUE, DEFAULT_NAME
#
# Expected call edges: none (leaf module — pure functions, no outgoing calls)
#
# Corner cases: module-level constants, pure utility functions with no dependencies
# Package: utils | imported_by: core
# ---
MAX_VALUE = 1000
MIN_VALUE = 0
DEFAULT_NAME = "unknown"


def add(a: int, b: int) -> int:
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b


def clamp(value: int, min_val: int, max_val: int) -> int:
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value


def format_name(first: str, last: str) -> str:
    return f"{first} {last}"
