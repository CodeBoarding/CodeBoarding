"""Instance, class, and static method call patterns."""

# Baseline (this file): references=8 classes=1 nodes=8 outgoing_edges=13 incoming_edges=13


class MethodPlayground:
    def instance_a(self) -> int:
        return self.instance_b()

    def instance_b(self) -> int:
        return self.static_c()

    @staticmethod
    def static_c() -> int:
        return utility_fn()

    @classmethod
    def class_d(cls) -> int:
        return cls.static_c()


def utility_fn() -> int:
    return 5


def invoke_method_variants(playground: MethodPlayground) -> int:
    return playground.instance_a() + playground.class_d()
