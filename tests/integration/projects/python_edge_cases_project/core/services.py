# --- Static Analysis Expected ---
#
# Defined entities:
#   Functions (7): log_call, create_dog, process_animals, build_profile,
#                  compute, make_multiplier, apply_config
#   Inner functions (2): wrapper (inside log_call), multiplier (inside make_multiplier)
#   Lambdas (1): sorter (module-level lambda)
#
# Expected call edges (from function bodies):
#   wrapper        → func()           (calls the wrapped function)
#   log_call       → wrapper          (returns inner function)
#   create_dog     → Dog.create()     (classmethod call, cross-module)
#   process_animals→ a.speak()        (method call on each animal)
#   build_profile  → UserProfile() + profile.set_address()  (cross-module)
#   compute        → add() + clamp()  (cross-module calls to utils.helpers)
#   make_multiplier→ multiplier       (returns closure)
#   apply_config   → config.debug, config.max_retries, config.tags  (attribute access)
#
# Corner cases: decorator (log_call wrapping create_dog), closure (make_multiplier→multiplier),
#   lambda (sorter), cross-module calls to utils.helpers
# Package: core | imports: utils
# ---
from core.base import Dog, Cat, Animal
from core.models import UserProfile, Config
from utils.helpers import add, clamp, MAX_VALUE


def log_call(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@log_call
def create_dog(name: str) -> Dog:
    return Dog.create(name)


def process_animals(animals: list[Animal]) -> list[str]:
    return [a.speak() for a in animals]


def build_profile(name: str, email: str, street: str, city: str) -> UserProfile:
    profile = UserProfile(name, email)
    profile.set_address(street, city)
    return profile


def compute(a: int, b: int) -> int:
    result = add(a, b)
    return clamp(result, 0, MAX_VALUE)


def make_multiplier(factor: int):
    def multiplier(x: int) -> int:
        return x * factor

    return multiplier


sorter = lambda items: sorted(items, key=lambda x: x.lower())


def apply_config(config: Config) -> dict:
    return {
        "debug": config.debug,
        "max_retries": config.max_retries,
        "tags": config.tags,
    }
