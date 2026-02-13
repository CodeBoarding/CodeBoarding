"""Inheritance and super() dispatch."""

# Baseline (this file): references=7 classes=2 nodes=8 outgoing_edges=9 incoming_edges=9


class Base:
    def process(self) -> int:
        return helper_base()


class Child(Base):
    def process(self) -> int:
        parent_value = super().process()
        return parent_value + helper_child()


def helper_base() -> int:
    return 10


def helper_child() -> int:
    return 3


def run_inheritance() -> int:
    child = Child()
    return child.process()
