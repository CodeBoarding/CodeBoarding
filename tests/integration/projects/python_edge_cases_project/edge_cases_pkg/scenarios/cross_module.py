"""Cross-file call targets."""

# Baseline (this file): references=2 classes=0 nodes=2 outgoing_edges=1 incoming_edges=1


def external_target() -> int:
    return terminal_external()


def terminal_external() -> int:
    return 7
