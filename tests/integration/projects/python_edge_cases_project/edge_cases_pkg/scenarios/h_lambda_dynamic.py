"""Lambda and dynamic dispatch scenarios."""

# Baseline (this file): references=16 classes=1 nodes=16 outgoing_edges=9 incoming_edges=9


def apply_with_lambda(values: list[int]) -> list[int]:
    transform = lambda item: local_transform(item)
    return [transform(value) for value in values]


def local_transform(item: int) -> int:
    return item + 1


class DynamicTarget:
    def ping(self) -> int:
        return local_transform(2)


def dynamic_dispatch(obj: object, method_name: str) -> int:
    method = getattr(obj, method_name)
    return method()


def create_runtime_class():
    generated = type("Generated", (DynamicTarget,), {"pong": lambda self: self.ping()})
    return generated


def run_dynamic() -> int:
    generated_class = create_runtime_class()
    instance = generated_class()
    return dynamic_dispatch(instance, "ping")
