# --- Static Analysis Expected ---
#
# Defined entities:
#   Classes (1): QueryBuilder
#   Methods (5): QueryBuilder.__init__, QueryBuilder.where, QueryBuilder.order_by,
#                QueryBuilder.limit, QueryBuilder.build
#   Functions (16): _handle_dog, _handle_cat, _handle_default, dispatch,
#                   build_query, get_profile_city, double, is_positive,
#                   transform_list, create_profile, create_and_summarize,
#                   generate_animals, animal_names, pipeline, compute_nested,
#                   conditional_dispatch
#   Constants (1): HANDLERS (dict dispatch table)
#
# Expected call edges (from function/method bodies):
#   _handle_dog          → Animal.speak()
#   _handle_cat          → Animal.speak()
#   _handle_default      → Animal.speak()
#   dispatch             → HANDLERS.get() — dict lookup, calls handler(animal)
#   build_query          → QueryBuilder() → .where() → .order_by() → .limit() → .build()
#   get_profile_city     → UserProfile() → .set_address() → .get_summary()
#   double               → add()              (cross-module call to utils.helpers)
#   is_positive          → subtract()         (cross-module call to utils.helpers)
#   transform_list       → filter(is_positive) + map(double)  (callables as args)
#   create_profile       → UserProfile()      (*args/**kwargs forwarded to constructor)
#   create_and_summarize → create_profile() → .get_summary()
#   generate_animals     → Dog() + Cat() + Duck()  (constructor calls)
#   animal_names         → format_name()       (cross-module call to utils.helpers)
#   pipeline             → generate_animals() → animal_names()
#   compute_nested       → add(subtract(), add())  (nested call expressions)
#   conditional_dispatch → format_name() + dispatch()
#
# Class hierarchy:
#   QueryBuilder — standalone, no inheritance (builder pattern)
#
# Corner cases: dict-based dispatch, method chaining (builder pattern),
#   chained attribute calls, map/filter with callables, *args/**kwargs forwarding,
#   generator pipeline, nested function calls, conditional call chains
# Package: core | imports: utils
# ---
from core.base import Dog, Cat, Duck, Animal
from core.models import UserProfile, Config
from utils.helpers import add, subtract, format_name


# --- Dict dispatch / strategy pattern ---


def _handle_dog(animal: Animal) -> str:
    return f"dog:{animal.speak()}"


def _handle_cat(animal: Animal) -> str:
    return f"cat:{animal.speak()}"


def _handle_default(animal: Animal) -> str:
    return f"other:{animal.speak()}"


HANDLERS: dict[type, object] = {
    Dog: _handle_dog,
    Cat: _handle_cat,
}


def dispatch(animal: Animal) -> str:
    handler = HANDLERS.get(type(animal), _handle_default)
    return handler(animal)


# --- Method chaining via builder ---


class QueryBuilder:
    def __init__(self):
        self._filters: list[str] = []
        self._sort_key: str | None = None
        self._limit: int | None = None

    def where(self, condition: str) -> "QueryBuilder":
        self._filters.append(condition)
        return self

    def order_by(self, key: str) -> "QueryBuilder":
        self._sort_key = key
        return self

    def limit(self, n: int) -> "QueryBuilder":
        self._limit = n
        return self

    def build(self) -> dict:
        return {
            "filters": self._filters,
            "sort": self._sort_key,
            "limit": self._limit,
        }


def build_query() -> dict:
    return QueryBuilder().where("active=true").order_by("name").limit(10).build()


# --- Chained attribute calls ---


def get_profile_city(name: str, email: str, street: str, city: str) -> str:
    profile = UserProfile(name, email)
    profile.set_address(street, city)
    return profile.get_summary().split(" - ")[1]


# --- map / filter with callables ---


def double(x: int) -> int:
    return add(x, x)


def is_positive(x: int) -> bool:
    return subtract(x, 0) > 0


def transform_list(values: list[int]) -> list[int]:
    positives = list(filter(is_positive, values))
    return list(map(double, positives))


# --- *args / **kwargs forwarding ---


def create_profile(*args, **kwargs) -> UserProfile:
    return UserProfile(*args, **kwargs)


def create_and_summarize(*args, **kwargs) -> str:
    profile = create_profile(*args, **kwargs)
    return profile.get_summary()


# --- Generator pipeline ---


def generate_animals() -> list[Animal]:
    return [Dog("Rex"), Cat("Whiskers"), Duck("Donald")]


def animal_names(animals: list[Animal]) -> list[str]:
    return [format_name(a.name, a.__class__.__name__) for a in animals]


def pipeline() -> list[str]:
    animals = generate_animals()
    names = animal_names(animals)
    return names


# --- Nested function calls (result of one call passed directly to another) ---


def compute_nested() -> int:
    return add(subtract(10, 3), add(1, 2))


# --- Conditional call chains ---


def conditional_dispatch(animal: Animal, verbose: bool = False) -> str:
    if verbose:
        name = format_name(animal.name, type(animal).__name__)
        return f"{name}: {dispatch(animal)}"
    return dispatch(animal)
